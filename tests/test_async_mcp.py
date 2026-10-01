"""MCP 生命周期测试：真实异步客户端/管理器配合内存中的 SDK 会话。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from CodingAgent.async_support import AsyncHookRegistry, AsyncToolDispatcher
from CodingAgent.mcp.async_client import AsyncMCPClient
from CodingAgent.mcp.async_manager import AsyncMCPManager
from CodingAgent.mcp.config import MCPServerConfig
from tests.async_helpers import AsyncIsolatedTestCase
from tests.helpers import tool_call


def page(*names, cursor=None):
    return SimpleNamespace(tools=[SimpleNamespace(
        name=name, description=name,
        input_schema={"type": "object", "properties": {}},
    ) for name in names], next_cursor=cursor)


class MemorySession:
    """只替换 SDK 的外部边界；记录打开和关闭它的实际 Task。"""

    def __init__(self):
        self.entered = asyncio.Event()
        self.closed = asyncio.Event()
        self.enter_task = None
        self.exit_task = None
        self.list_tools = AsyncMock(return_value=page("lookup"))
        self.call_tool = AsyncMock(return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="offline evidence")], is_error=False))

    @asynccontextmanager
    async def open(self, target):
        self.enter_task = asyncio.current_task()
        self.entered.set()
        try:
            yield self
        finally:
            self.exit_task = asyncio.current_task()
            self.closed.set()


class AsyncMCPClientTests(AsyncIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.session = MemorySession()
        self.factory = self.enterContext(patch(
            "CodingAgent.mcp.async_client.Client", side_effect=self.session.open))
        self.client = AsyncMCPClient(MCPServerConfig(
            name="local", command="offline-server", args=["--test"]))
        self.addAsyncCleanup(self.client.disconnect, timeout=0.01)

    async def test_connection_stays_open_and_owner_closes_its_own_context(self):
        self.session.list_tools.side_effect = [page("first", cursor="next"), page("second")]
        tools = await self.client.connect()
        owner = self.client._owner
        self.assertEqual([tool["name"] for tool in tools], ["first", "second"])
        self.assertEqual(self.session.list_tools.await_args_list[1].kwargs, {"cursor": "next"})
        self.assertTrue(self.client.is_connected())
        self.assertFalse(self.session.closed.is_set())
        self.assertIs(self.session.enter_task, owner)
        self.assertIsNot(owner, asyncio.current_task())
        self.assertIs(await self.client.connect(), tools)
        self.factory.assert_called_once()
        target = self.factory.call_args.args[0]
        self.assertEqual(target.command, "offline-server")
        self.assertEqual(await self.client.call_tool("first", {"key": "x"}), "offline evidence")
        self.session.call_tool.assert_awaited_once_with("first", {"key": "x"})
        await self.client.disconnect()
        self.assertTrue(owner.done())
        self.assertIs(self.session.exit_task, self.session.enter_task)
        self.assertFalse(self.client.is_connected())
        self.assertEqual(self.client.tools, [])
        self.assertIsNone(self.client._owner)
        await self.client.disconnect()  # 重复关闭没有副作用。

    async def test_discovery_failure_cleans_up_and_can_reconnect(self):
        self.session.list_tools.side_effect = ValueError("discovery failed")
        with self.assertRaisesRegex(RuntimeError, "discovery failed"):
            await asyncio.wait_for(self.client.connect(), 2)
        self.assertTrue(self.client._ready.is_set())
        self.assertTrue(self.session.closed.is_set())
        self.assertIsNone(self.client._owner)
        self.assertFalse(self.client.is_connected())
        self.session.list_tools.side_effect = None
        self.assertEqual((await self.client.connect())[0]["name"], "lookup")

    async def test_enter_failure_wakes_connect_without_waiting_for_timeout(self):
        @asynccontextmanager
        async def fail(target):
            raise OSError("cannot open")
            yield  # 保持为异步上下文管理器的生成器。

        self.factory.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, "cannot open"):
            await asyncio.wait_for(self.client.connect(timeout=30), 2)
        self.assertIsNone(self.client._owner)

    async def test_connect_timeout_cancels_stalled_owner_and_closes_context(self):
        async def stall(**kwargs):
            await asyncio.Event().wait()

        self.session.list_tools.side_effect = stall
        original_disconnect = self.client.disconnect

        async def short_grace():
            await original_disconnect(timeout=0.01)

        # 缩短清理宽限期；连接、取消与上下文退出仍执行真实代码。
        with patch.object(self.client, "disconnect", side_effect=short_grace):
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(self.client.connect(timeout=0.02), 2)
        self.assertTrue(self.session.closed.is_set())
        self.assertIs(self.session.enter_task, self.session.exit_task)
        self.assertIsNone(self.client._owner)

    async def test_cancel_connect_does_not_leave_an_unregistered_session(self):
        async def stall(**kwargs):
            await asyncio.Event().wait()

        self.session.list_tools.side_effect = stall
        original_disconnect = self.client.disconnect

        async def short_grace():
            await original_disconnect(timeout=0.01)

        with patch.object(self.client, "disconnect", side_effect=short_grace):
            task = asyncio.create_task(self.client.connect())
            try:
                await asyncio.wait_for(self.session.entered.wait(), 2)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
        self.assertTrue(self.session.closed.is_set())
        self.assertIsNone(self.client._owner)

    async def test_cancelling_disconnect_waits_for_context_cleanup(self):
        closing, release = asyncio.Event(), asyncio.Event()

        @asynccontextmanager
        async def slow_close(target):
            try:
                yield self.session
            finally:
                closing.set()
                await release.wait()
                self.session.closed.set()

        self.factory.side_effect = slow_close
        await self.client.connect()
        owner = self.client._owner
        task = asyncio.create_task(self.client.disconnect())
        try:
            await asyncio.wait_for(closing.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertFalse(owner.done())
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        self.assertTrue(self.session.closed.is_set())
        self.assertTrue(owner.done())
        self.assertIsNone(self.client._owner)

    async def test_tool_timeout_cancels_request_but_keeps_session_available(self):
        request_finished = asyncio.Event()

        async def stall(*args):
            try:
                await asyncio.Event().wait()
            finally:
                request_finished.set()

        await self.client.connect()
        self.session.call_tool.side_effect = stall
        self.assertIn("timed out", await self.client.call_tool("lookup", timeout=0.01))
        self.assertTrue(request_finished.is_set())
        self.assertTrue(self.client.is_connected())
        self.session.call_tool.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.client.call_tool("lookup")
        self.session.call_tool.side_effect = ValueError("bad request")
        self.assertIn("ValueError: bad request", await self.client.call_tool("lookup"))

    async def test_call_before_connection_and_structured_error_result(self):
        self.assertIn("not connected", await self.client.call_tool("lookup"))
        self.session.call_tool.assert_not_called()
        await self.client.connect()
        self.session.call_tool.return_value = SimpleNamespace(
            content=[], structured_content={"detail": "failed"}, is_error=True)
        self.assertEqual(await self.client.call_tool("lookup"), 'MCP error: {"detail": "failed"}')

    async def test_http_headers_are_passed_to_transport_and_contexts_close(self):
        events = []
        http = Mock()

        @asynccontextmanager
        async def open_http(**kwargs):
            events.append("http-open")
            try:
                yield http
            finally:
                events.append("http-close")

        @asynccontextmanager
        async def open_sdk(target):
            events.append("session-open")
            try:
                yield self.session
            finally:
                events.append("session-close")

        client = AsyncMCPClient(MCPServerConfig(
            name="remote", url="https://example.invalid/mcp", headers={"X-Test": "fake"}))
        self.factory.side_effect = open_sdk
        with patch("httpx2.AsyncClient", side_effect=open_http) as http_factory, patch(
            "mcp.client.streamable_http.streamable_http_client", return_value="fake-transport"
        ) as transport:
            try:
                await client.connect()
                http_factory.assert_called_once()
                self.assertEqual(http_factory.call_args.kwargs["headers"], {"X-Test": "fake"})
                transport.assert_called_once_with("https://example.invalid/mcp", http_client=http)
                self.factory.assert_called_once_with("fake-transport")
            finally:
                await client.disconnect()
        self.assertEqual(events, ["http-open", "session-open", "session-close", "http-close"])


class AsyncMCPManagerTests(AsyncIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.config = MCPServerConfig(name="local", command="offline-server")
        self.manager = AsyncMCPManager({"local": self.config}, host_policy={})
        self.addAsyncCleanup(self.manager.disconnect_all_mcp)

    async def test_concurrent_connects_share_one_persistent_client(self):
        session = MemorySession()
        listing, release = asyncio.Event(), asyncio.Event()

        async def list_tools(**kwargs):
            listing.set()
            await release.wait()
            return page("lookup")

        session.list_tools.side_effect = list_tools
        with patch("CodingAgent.mcp.async_client.Client", side_effect=session.open) as factory:
            first = asyncio.create_task(self.manager.connect_mcp("local"))
            second = None
            try:
                await asyncio.wait_for(listing.wait(), 2)
                self.assertEqual(self.manager.clients, {})  # 发现成功前不发布。
                second = asyncio.create_task(self.manager.connect_mcp("local"))
                await asyncio.sleep(0)
                factory.assert_called_once()
            finally:
                release.set()
                results = await asyncio.wait_for(asyncio.gather(
                    first, *([second] if second else [])), 2)
            self.assertIn("Connected", results[0])
            self.assertIn("already connected", results[1])
            self.assertEqual(self.manager.connected_servers(), ["local"])
            tools, handlers = self.manager.assemble_tool_pool([], {})
            self.assertIn("mcp__local__lookup", [tool["name"] for tool in tools])
            self.assertEqual(self.manager.get_tool_policy("mcp__local__lookup"), "confirm")
            result = await AsyncToolDispatcher(AsyncHookRegistry(), Mock()).execute_tool(
                tool_call("mcp__local__lookup", {"key": "x"}), handlers)
            self.assertEqual(result, "offline evidence")
            await self.manager.disconnect_all_mcp()
        self.assertTrue(session.closed.is_set())
        self.assertEqual(self.manager.clients, {})
        self.assertEqual(self.manager.tool_policies, {})

    async def test_failed_connection_is_not_published(self):
        session = MemorySession()
        session.list_tools.side_effect = RuntimeError("discovery failed")
        with patch("CodingAgent.mcp.async_client.Client", side_effect=session.open):
            self.assertIn("discovery failed", await self.manager.run_connect_mcp("local"))
        self.assertEqual(self.manager.clients, {})
        self.assertTrue(session.closed.is_set())
        self.assertIn("not found", await self.manager.run_connect_mcp("missing"))

    async def test_stale_client_is_closed_before_replacement(self):
        stale = Mock()
        stale.is_connected.return_value = False
        stale.disconnect = AsyncMock()
        self.manager.clients["local"] = stale
        session = MemorySession()
        with patch("CodingAgent.mcp.async_client.Client", side_effect=session.open):
            await self.manager.connect_mcp("local")
        stale.disconnect.assert_awaited_once()
        self.assertIsNot(self.manager.clients["local"], stale)

    async def test_one_close_failure_does_not_skip_other_servers(self):
        broken = SimpleNamespace(disconnect=AsyncMock(side_effect=RuntimeError("close failed")))
        healthy = SimpleNamespace(disconnect=AsyncMock())
        self.manager.clients.update(broken=broken, healthy=healthy)
        self.manager.tool_policies["mcp__healthy__lookup"] = "allow"
        await self.manager.disconnect_all_mcp()
        healthy.disconnect.assert_awaited_once()
        self.assertEqual(self.manager.clients, {})
        self.assertEqual(self.manager.tool_policies, {})
