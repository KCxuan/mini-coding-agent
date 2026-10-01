"""线程适配、异步输入、权限和工具分发的行为测试。"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from CodingAgent.async_support import (
    AsyncConsole, AsyncHookRegistry, AsyncPermissionManager,
    AsyncToolDispatcher, finish_task, run_sync,
)
from tests.async_helpers import AsyncIsolatedTestCase
from tests.helpers import tool_call


class ThreadAdapterTests(AsyncIsolatedTestCase):
    async def test_sync_work_runs_off_loop_and_forwards_arguments(self):
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = threading.Event()
        main_thread = threading.get_ident()

        def work(value, *, suffix):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(3):
                raise AssertionError("Test did not release worker")
            return value + suffix, threading.get_ident()

        task = asyncio.create_task(run_sync(work, "a", suffix="b"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            # 工作线程还未结束，事件循环已能恢复本测试。
            self.assertFalse(task.done())
        finally:
            release.set()
        value, thread_id = await asyncio.wait_for(task, 2)
        self.assertEqual(value, "ab")
        self.assertNotEqual(thread_id, main_thread)

    async def test_cancel_waits_for_worker_to_finish_before_propagating(self):
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release = threading.Event()
        finished = threading.Event()

        def work():
            loop.call_soon_threadsafe(entered.set)
            try:
                if not release.wait(3):
                    raise AssertionError("Test did not release worker")
            finally:
                finished.set()

        task = asyncio.create_task(run_sync(work))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # 重复取消也不能跳过正在进行的同步工作。
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertFalse(finished.is_set())
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        self.assertTrue(finished.is_set())

    async def test_sync_error_reaches_caller(self):
        def fail():
            raise ValueError("worker failed")

        with self.assertRaisesRegex(ValueError, "worker failed"):
            await run_sync(fail)

    async def test_cancelled_inner_task_does_not_spin_forever(self):
        inner = asyncio.create_task(asyncio.sleep(60))
        inner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(finish_task(inner), 2)

    async def test_outer_cancellation_still_wins_if_inner_later_fails(self):
        release = asyncio.Event()
        started = asyncio.Event()

        async def work():
            started.set()
            await release.wait()
            raise ValueError("late cleanup error")

        inner = asyncio.create_task(work())
        outer = asyncio.create_task(finish_task(inner))
        try:
            await asyncio.wait_for(started.wait(), 2)
            outer.cancel()
            await asyncio.sleep(0)
            self.assertFalse(inner.cancelled())
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(outer, 2)
        self.assertIsInstance(inner.exception(), ValueError)


class ConsoleAndHookTests(AsyncIsolatedTestCase):
    async def test_prompts_are_serial_and_waiting_input_yields(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        prompts = []

        async def prompt(text):
            prompts.append(text)
            if text == "first":
                entered.set()
                await release.wait()
            return text + " answer"

        session = SimpleNamespace(prompt_async=AsyncMock(side_effect=prompt))
        with patch("CodingAgent.async_support.PromptSession", return_value=session):
            console = AsyncConsole()
        first = asyncio.create_task(console.read("first"))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(console.read("second"))
            await asyncio.sleep(0)
            self.assertEqual(prompts, ["first"])
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertEqual(await asyncio.wait_for(second, 2), "second answer")
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)

    async def test_hooks_await_coroutine_returned_by_lambda_and_short_circuit(self):
        hooks = AsyncHookRegistry()
        order = []

        async def decide():
            await asyncio.sleep(0)
            order.append("permission")
            return "denied"

        hooks.register("PreToolUse", lambda block: order.append("log"))
        hooks.register("PreToolUse", lambda block: decide())
        after = Mock()
        hooks.register("PreToolUse", after)
        self.assertEqual(await hooks.trigger_async("PreToolUse", tool_call()), "denied")
        self.assertEqual(order, ["log", "permission"])
        after.assert_not_called()


class PermissionAndDispatcherTests(AsyncIsolatedTestCase):
    def permissions(self, answer="y", policy="confirm"):
        self.console = SimpleNamespace(read=AsyncMock(return_value=answer))
        return AsyncPermissionManager(
            self.workdir, get_mcp_policy=lambda name: policy, console=self.console)

    async def test_deny_list_never_prompts_or_starts_background_command(self):
        permissions = self.permissions()
        hooks = AsyncHookRegistry()
        hooks.register("PreToolUse", permissions.check_permission_async)
        background, handler = Mock(), Mock()
        output = await AsyncToolDispatcher(hooks, background).execute_tool(
            tool_call("bash", {"command": "sudo something", "run_in_background": True}),
            {"bash": handler})
        self.assertIn("deny list", output)
        self.console.read.assert_not_called()
        background.start.assert_not_called()
        handler.assert_not_called()

    async def test_workspace_boundary_and_mcp_policy_keep_existing_rules(self):
        permissions = self.permissions(answer="n")
        self.assertIsNone(await permissions.check_permission_async(tool_call()))
        self.console.read.assert_not_called()
        self.assertEqual(await permissions.check_permission_async(
            tool_call("read_file", {"path": str(self.root / "outside.txt")})),
            "Access outside workspace")
        self.console.read.assert_awaited_once()
        permissions = self.permissions(policy="allow")
        self.assertIsNone(await permissions.check_permission_async(tool_call("mcp__one__read", {})))
        self.console.read.assert_not_called()
        permissions = self.permissions(answer=" YES ")
        self.assertIsNone(await permissions.check_permission_async(tool_call("mcp__one__read", {})))
        self.console.read.assert_awaited_once()

    async def test_eof_denies_and_background_thread_cannot_prompt(self):
        permissions = self.permissions()
        self.console.read.side_effect = EOFError
        self.assertEqual(await permissions.ask_user("bash", {}, "confirm"), "deny")
        self.console.read.reset_mock()
        # 工作线程本身不能直接发起终端审批，沿用原有约束。
        def ask_from_thread():
            coroutine = permissions.ask_user("bash", {}, "confirm")
            try:
                # 此分支应在第一次 await 前直接返回。手动推进一次，避免
                # 在 Windows 上为新事件循环创建 socketpair（测试禁止联网）。
                try:
                    coroutine.send(None)
                except StopIteration as done:
                    return done.value
                raise AssertionError("Worker-thread approval unexpectedly waited for input")
            finally:
                coroutine.close()

        result = await asyncio.to_thread(ask_from_thread)
        self.assertEqual(result, "deny")
        self.console.read.assert_not_called()

    async def test_background_start_happens_only_after_approval(self):
        entered, answer = asyncio.Event(), asyncio.Event()

        async def read(prompt):
            entered.set()
            await answer.wait()
            return "y"

        permissions = self.permissions()
        self.console.read.side_effect = read
        hooks = AsyncHookRegistry()
        hooks.register("PreToolUse", permissions.check_permission_async)
        post = AsyncMock()
        hooks.register("PostToolUse", post)
        background, handler = Mock(), Mock()
        background.start.return_value = "bg_test"
        block = tool_call("bash", {"command": "rm sample.txt", "run_in_background": True})
        task = asyncio.create_task(AsyncToolDispatcher(hooks, background).execute_tool(
            block, {"bash": handler}))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            background.start.assert_not_called()
        finally:
            answer.set()
        output = await asyncio.wait_for(task, 2)
        self.assertIn("bg_test", output)
        background.start.assert_called_once_with(block)
        handler.assert_not_called()
        post.assert_awaited_once_with(block, output)

    async def test_threaded_tool_runs_after_permission_and_before_post_hook(self):
        order = []
        main_thread = threading.get_ident()
        hooks = AsyncHookRegistry()
        hooks.register("PreToolUse", lambda block: order.append(("pre", threading.get_ident())))
        hooks.register("PostToolUse", lambda block, result: order.append(("post", threading.get_ident())))

        def read_file(path):
            order.append((path, threading.get_ident()))
            return "evidence"

        result = await AsyncToolDispatcher(hooks, Mock()).execute_tool(
            tool_call(), {"read_file": read_file})
        self.assertEqual(result, "evidence")
        self.assertEqual([name for name, _ in order], ["pre", "sample.txt", "post"])
        self.assertEqual(order[0][1], main_thread)
        self.assertNotEqual(order[1][1], main_thread)
        self.assertEqual(order[2][1], main_thread)

    async def test_lambda_awaitable_sync_manager_and_tool_errors(self):
        dispatcher = AsyncToolDispatcher(AsyncHookRegistry(), Mock())
        external = AsyncMock(return_value="MCP result")
        output = await dispatcher.execute_tool(tool_call("mcp__one__read", {"key": "x"}),
            {"mcp__one__read": lambda **kwargs: external(**kwargs)})
        self.assertEqual(output, "MCP result")
        external.assert_awaited_once_with(key="x")
        main_thread = threading.get_ident()
        self.assertEqual(await dispatcher.execute_tool(tool_call("subagent_status", {}),
            {"subagent_status": lambda: threading.get_ident()}), str(main_thread))
        self.assertEqual(await dispatcher.execute_tool(tool_call("missing", {}), {}), "Unknown: missing")
        self.assertEqual(await dispatcher.execute_tool(tool_call("mcp__one__read", {}),
            {"mcp__one__read": AsyncMock(side_effect=ValueError("failed"))}), "Error: failed")

    async def test_cancelled_tool_propagates_and_does_not_run_post_hook(self):
        hooks = AsyncHookRegistry()
        post = Mock()
        hooks.register("PostToolUse", post)
        with self.assertRaises(asyncio.CancelledError):
            await AsyncToolDispatcher(hooks, Mock()).execute_tool(
                tool_call("mcp__one__read", {}),
                {"mcp__one__read": AsyncMock(side_effect=asyncio.CancelledError)})
        post.assert_not_called()

    async def test_background_can_be_disabled_for_foreground_execution(self):
        background, handler = Mock(), Mock(return_value="foreground result")
        output = await AsyncToolDispatcher(AsyncHookRegistry(), background).execute_tool(
            tool_call("bash", {"command": "fake", "run_in_background": True}),
            {"bash": handler}, allow_background=False)
        self.assertEqual(output, "foreground result")
        background.start.assert_not_called()
        handler.assert_called_once_with(command="fake", run_in_background=True)
