# 异步版本的MCP客户端
import asyncio
from contextlib import asynccontextmanager

from mcp import Client

from ..async_support import finish_task
from .client import MCPClient
from .config import MCPServerConfig

class AsyncMCPClient(MCPClient):
    def __init__(self, config: MCPServerConfig):
        # 不调用旧构造函数，不创建或接收 AsyncBridge。
        self.config = config
        self.name = config.name
        self.transport_kind = config.transport_kind

        self.tools = []
        self._session = None

        # _owner 保存 “负责持有 MCP 会话的异步任务”
        self._owner = None
        self._ready = None
        self._stop = None
        self._error = None

    def is_connected(self):
        return (
            self._session is not None
            and self._owner is not None
            and not self._owner.done()
        )

    @asynccontextmanager
    async def _open_session(self):
        """
        保留现有 HTTP headers / stdio / URL 连接逻辑。
        打开、使用、关闭上下文都由 _serve 所在任务负责。
        """
        if self.config.url and self.config.headers:
            import httpx2
            from mcp.client.streamable_http import (
                streamable_http_client,
            )

            timeout = httpx2.Timeout(30.0, read=300.0)

            async with httpx2.AsyncClient(
                headers=self.config.headers,
                timeout=timeout,
            ) as http:
                transport = streamable_http_client(
                    self.config.url,
                    http_client=http,
                )

                async with Client(transport) as session:
                    yield session

            return

        async with Client(self._target()) as session:
            yield session

    async def _serve(self):
        """
        持有会话，直到收到关闭信号。
        打开会话、发现工具、保持会话打开、等待关闭信号、关闭会话。
        """
        try:
            async with self._open_session() as session:
                tools = await self._list_all_tools(session)

                # 工具发现成功后，才发布已连接状态。
                self.tools = tools
                self._session = session
                self._ready.set()

                await self._stop.wait()

        except Exception as exc:
            self._error = (
                f"MCP server {self.name!r}: "
                f"{type(exc).__name__}: {exc}"
            )

        finally:
            self._session = None
            self.tools = []

            # 连接失败或被取消时，也唤醒等待连接的调用方。
            self._ready.set()

    async def connect(self, timeout=60.0):
        if self.is_connected():
            return self.tools

        if self._owner is not None:
            await self.disconnect()

        self._error = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()

        self._owner = asyncio.create_task(
            self._serve(),
            name=f"mcp-session:{self.name}",
        )

        try:
            await asyncio.wait_for(
                self._ready.wait(),
                timeout=timeout,
            )

            if self._error:
                raise RuntimeError(self._error)

            if not self.is_connected():
                raise RuntimeError(
                    f"MCP server {self.name!r} "
                    "closed before becoming ready"
                )

            return self.tools

        except BaseException:
            # 包括调用方取消，不能留下未登记的连接任务。
            await self.disconnect()
            raise

    async def call_tool(
        self,
        tool_name,
        args=None,
        timeout=120.0,
    ):
        session = self._session

        if not self.is_connected():
            return (
                f"MCP error: server {self.name!r} "
                "is not connected"
            )

        try:
            result = await asyncio.wait_for(
                session.call_tool(tool_name, args or {}),
                timeout=timeout,
            )
            return self._format_mcp_tool_result(result)

        except TimeoutError:
            return (
                f"MCP error: calling {tool_name!r} "
                f"on {self.name!r} timed out after {timeout}s"
            )

        except Exception as exc:
            return f"MCP error: {type(exc).__name__}: {exc}"

    async def _close_owner(self, owner, timeout):
        try:
            done, _ = await asyncio.wait(
                {owner},
                timeout=timeout,
            )

            if not done:
                owner.cancel()

            try:
                await owner
            except asyncio.CancelledError:
                # 这里接收的是被关闭的会话任务的取消。
                pass

            if self._error:
                print(f"  [mcp] {self._error}")

        finally:
            self._owner = None
            self._session = None
            self.tools = []

    async def disconnect(self, timeout=10.0):
        owner = self._owner
        if owner is None:
            return

        self._stop.set()

        close_task = asyncio.create_task(
            self._close_owner(owner, timeout),
            name=f"mcp-close:{self.name}",
        )
        await finish_task(close_task)

