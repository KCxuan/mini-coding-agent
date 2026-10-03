"""异步入口、主循环和退出清理；从源文件提取函数，不运行顶层初始化。"""

import asyncio
import json
import re
import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from CodingAgent.async_support import (
    AsyncConsole, AsyncHookRegistry, AsyncPermissionManager,
    AsyncToolDispatcher, finish_task, run_sync,
)
from CodingAgent.context_budget import ContextBudgetError, estimate_request_tokens
from CodingAgent.hooks import DefaultHooks, HookRegistry
from CodingAgent.images import tool_result_preview
from CodingAgent.subagent.manager import SubagentManager
from CodingAgent.taskboard import TaskBoard, TaskStore
from CodingAgent.tools.adapters import SubagentTools
from CodingAgent.tools.files import FileTools
from tests.async_helpers import AsyncIsolatedTestCase, AsyncScriptedClient, load_async_main
from tests.helpers import text_response, tool_call, tool_response


class AsyncMainTests(AsyncIsolatedTestCase):
    def namespace(self):
        background = Mock()
        background.inject_background_results.return_value = 0
        background.has_running.return_value = False
        subagents = Mock()
        subagents.has_running.return_value = False
        subagents.shutdown.return_value = []
        subagents.collect.return_value = []
        subagent_tools = Mock()
        subagent_tools.inject_subagent_results.return_value = 0
        memory = Mock()
        memory.load_memories.return_value = "recalled evidence"
        memory.extract_memories.return_value = False
        compactor = Mock()
        compactor.prepare.side_effect = lambda messages, request, **kwargs: messages
        compactor.reactive_compact.side_effect = lambda messages, request: messages
        mcp = Mock()
        mcp.assemble_tool_pool.side_effect = lambda **kwargs: (
            kwargs["builtin_tools"], kwargs["builtin_handlers"])
        mcp.disconnect_all_mcp = AsyncMock()
        default_hooks = DefaultHooks(self.workdir)
        hooks = HookRegistry()
        # 使用真实默认 Stop hook，避免替身掩盖参数不兼容。
        stop = Mock(wraps=default_hooks.summary_hook)
        hooks.register("Stop", stop)
        ns = {
            "asyncio": asyncio, "json": json, "run_sync": run_sync,
            "finish_task": finish_task, "AsyncConsole": AsyncConsole,
            "AsyncPermissionManager": AsyncPermissionManager,
            "AsyncHookRegistry": AsyncHookRegistry, "AsyncToolDispatcher": AsyncToolDispatcher,
            "MODEL": "offline-model", "WORKDIR": self.workdir, "MAX_SUBAGENTS": 4,
            "MAX_REACTIVE_RETRIES": 1, "PROJECT_INSTRUCTIONS": "project rules",
            "CONFIG": SimpleNamespace(context_window_tokens=1_000_000),
            "ContextBudgetError": ContextBudgetError,
            "estimate_request_tokens": estimate_request_tokens,
            "tool_result_preview": tool_result_preview,
            "BACKGROUND": background, "SUBAGENTS": subagents,
            "SUBAGENT_TOOLS": subagent_tools, "MEMORY_MANAGER": memory,
            "MEMORY_STORE": Mock(), "SKILL_LOADER": Mock(), "COMPACTOR": compactor,
            "MCP_MANAGER": mcp, "HOOKS": hooks, "DEFAULT_HOOKS": default_hooks,
            "TOOLS": [], "TOOL_HANDLERS": {"read_file": FileTools(self.workdir).run_read_file},
            "build_system_prompt": Mock(return_value="offline prompt"),
            "SHELL": Mock(), "client": Mock(), "_cleanup_started": False,
            "_exit_requested": False, "format_subagent_result": lambda state: state.summary,
        }
        load_async_main(ns, "async_agent_loop", "inject_async_results",
            "async_request_usable_response", "confirm_more_rounds",
            "IncompleteResponseError", "AgentRoundLimitError", "MAIN_OUTPUT_TOKENS",
            "MAIN_RETRY_OUTPUT_TOKENS", "MAX_RESPONSE_RETRIES", "AGENT_ROUND_BATCH",
            "cleanup_program_async", "async_main")
        ns["test_stop"] = stop
        return ns

    async def run_loop(self, ns, llm, *, dispatcher=None):
        messages = [{"role": "user", "content": "Read the evidence"}]
        console = SimpleNamespace(read=AsyncMock(side_effect=AssertionError("Unexpected prompt")))
        await asyncio.wait_for(ns["async_agent_loop"](
            messages, "Read the evidence", llm=llm, console=console,
            dispatcher=dispatcher or AsyncToolDispatcher(AsyncHookRegistry(), ns["BACKGROUND"])), 3)
        return messages

    async def test_tool_result_reaches_model_and_sync_memory_runs_off_loop(self):
        (self.workdir / "sample.txt").write_text("file evidence", encoding="utf-8")
        ns = self.namespace()
        main_thread = threading.get_ident()
        worker_threads = []

        def record(result):
            def callback(*args, **kwargs):
                worker_threads.append(threading.get_ident())
                return result
            return callback

        ns["MEMORY_MANAGER"].load_memories.side_effect = record("remembered")
        ns["MEMORY_MANAGER"].extract_memories.side_effect = record(True)
        ns["MEMORY_MANAGER"].consolidate_memories.side_effect = record(None)
        llm = AsyncScriptedClient(tool_response(tool_call()), text_response("done"))
        messages = await self.run_loop(ns, llm)
        self.assertEqual(len(llm.calls), 2)
        result = llm.calls[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "call_1")
        self.assertIn("file evidence", result["content"])
        self.assertEqual(ns["test_stop"].call_args.args, (messages, 1))
        self.assertEqual(len(worker_threads), 3)
        self.assertTrue(all(identity != main_thread for identity in worker_threads))
        self.assertEqual(ns["COMPACTOR"].prepare.call_args.kwargs["output_reserve"], 32768)
        self.assertEqual(ns["build_system_prompt"].call_args.kwargs["project_instructions"], "project rules")

    async def test_tools_in_one_response_remain_sequential(self):
        ns = self.namespace()
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def first():
            order.append("first-start")
            entered.set()
            await release.wait()
            order.append("first-end")
            return "one"

        async def second():
            order.append("second")
            return "two"

        ns["TOOL_HANDLERS"] = {"mcp__one__first": first, "mcp__one__second": second}
        llm = AsyncScriptedClient(tool_response(
            tool_call("mcp__one__first", {}, "one"),
            tool_call("mcp__one__second", {}, "two")), text_response("done"))
        task = asyncio.create_task(self.run_loop(ns, llm))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertEqual(order, ["first-start"])
            self.assertEqual(len(llm.calls), 1)
        finally:
            release.set()
            await task
        self.assertEqual(order, ["first-start", "first-end", "second"])
        self.assertEqual([r["tool_use_id"] for r in llm.calls[1]["messages"][-1]["content"]], ["one", "two"])

    async def test_truncated_tools_are_never_executed_and_retry_uses_larger_budget(self):
        ns = self.namespace()
        dispatcher = SimpleNamespace(execute_tool=AsyncMock())
        llm = AsyncScriptedClient(tool_response(tool_call(), stop_reason="max_tokens"), text_response("complete"))
        messages = await self.run_loop(ns, llm, dispatcher=dispatcher)
        dispatcher.execute_tool.assert_not_called()
        self.assertEqual([c["max_tokens"] for c in llm.calls], [16384, 32768])
        self.assertEqual(len(messages), 2)
        self.assertEqual(llm.calls[0]["messages"], llm.calls[1]["messages"])

    async def test_empty_response_retry_is_bounded_and_invalid_response_fails(self):
        for responses in ((text_response(""), text_response(" ")),
                          (text_response("x", stop_reason="unknown"),),
                          (tool_response(tool_call(), stop_reason="end_turn"),)):
            with self.subTest(responses=len(responses), reason=responses[0].stop_reason):
                ns = self.namespace()
                llm = AsyncScriptedClient(*responses)
                with self.assertRaises(ns["IncompleteResponseError"]):
                    await self.run_loop(ns, llm)
                self.assertEqual(len(llm.calls), len(responses))
                ns["MEMORY_MANAGER"].extract_memories.assert_not_called()

    async def test_reactive_compaction_is_awaited_and_retries_are_bounded(self):
        ns = self.namespace()
        llm = AsyncScriptedClient(RuntimeError("prompt_too_long"), text_response("done"))
        await self.run_loop(ns, llm)
        ns["COMPACTOR"].reactive_compact.assert_called_once()
        self.assertEqual(len(llm.calls), 2)
        ns = self.namespace()
        llm = AsyncScriptedClient(RuntimeError("too many tokens"), RuntimeError("too many tokens"))
        with self.assertRaisesRegex(RuntimeError, "too many tokens"):
            await self.run_loop(ns, llm)
        ns["COMPACTOR"].reactive_compact.assert_called_once()
        self.assertEqual(len(llm.calls), 2)

    async def test_explicit_compact_runs_after_tool_result_is_recorded(self):
        ns = self.namespace()
        ns["TOOL_HANDLERS"]["compact"] = lambda: "compact requested"
        seen = []

        def compact(messages, request):
            seen.append(messages[-1]["content"][0]["tool_use_id"])
            return messages

        ns["COMPACTOR"].reactive_compact.side_effect = compact
        await self.run_loop(ns, AsyncScriptedClient(
            tool_response(tool_call("compact", {}, "compact_1")), text_response("done")))
        self.assertEqual(seen, ["compact_1"])

    async def test_background_wait_yields_without_repeated_model_requests(self):
        for kind in ("BACKGROUND", "SUBAGENTS"):
            with self.subTest(kind=kind):
                ns = self.namespace()
                ready = asyncio.Event()
                delivered = False
                status_checks = 0
                loop = asyncio.get_running_loop()
                llm = AsyncScriptedClient(text_response("waiting"), text_response("integrated"))

                def has_running():
                    nonlocal status_checks
                    status_checks += 1
                    if status_checks > 10:
                        raise AssertionError("Background wait did not yield or collect its result")
                    # 只有让出事件循环，通知才能到达；忙等或 time.sleep 会使测试失败。
                    loop.call_soon(ready.set)
                    return not ready.is_set()

                def inject(messages, **kwargs):
                    nonlocal delivered
                    if ready.is_set() and not delivered:
                        self.assertEqual(len(llm.calls), 1)
                        messages.append({"role": "user", "content": "late evidence"})
                        delivered = True
                        return 1
                    return 0

                ns[kind].has_running.side_effect = has_running
                if kind == "BACKGROUND":
                    ns[kind].inject_background_results.side_effect = inject
                else:
                    ns["SUBAGENT_TOOLS"].inject_subagent_results.side_effect = inject
                await self.run_loop(ns, llm)
                self.assertEqual(len(llm.calls), 2)
                self.assertEqual(sum(m["content"] == "late evidence" for m in llm.calls[1]["messages"]), 1)

    async def test_existing_subagent_thread_delivers_result_and_tool_counts(self):
        ns = self.namespace()
        board = TaskBoard(TaskStore(self.workdir / "tasks", workdir=self.workdir))
        task = board.create_task("Read-only review")
        board.claim_task(task.id)
        release = threading.Event()

        def execute(state):
            if not release.wait(3):
                raise AssertionError("Test did not release subagent")
            state.status = "completed"
            state.summary = "SUBAGENT_EVIDENCE"
            state.remaining = ""
            state.tool_call_count = 3

        manager = SubagentManager(board, SimpleNamespace(execute_subagent=execute),
            workdir=self.workdir, max_workers=1)
        adapter = SubagentTools(manager)
        ns["SUBAGENTS"] = manager
        ns["SUBAGENT_TOOLS"] = adapter
        ns["TOOL_HANDLERS"]["task"] = adapter.run_subagent
        loop = asyncio.get_running_loop()

        def waiting_response():
            loop.call_soon(release.set)
            return text_response("waiting for subagent")

        llm = AsyncScriptedClient(tool_response(tool_call("task", {
            "task_id": task.id, "prompt": "Read source"})),
            waiting_response, text_response("integrated"))
        try:
            with patch("builtins.print") as printed:
                await self.run_loop(ns, llm)
            self.assertEqual(len(llm.calls), 3)
            final_request = json.dumps(llm.calls[-1]["messages"], default=vars, ensure_ascii=False)
            self.assertEqual(final_request.count("SUBAGENT_EVIDENCE"), 1)
            self.assertEqual(manager.collect(), [])
            self.assertFalse(manager.has_running())
            self.assertIn("[tools] 主 Agent: 1；子 Agent: 3；合计: 4",
                [re.sub(r"\x1b\[[0-9;]*m", "", call.args[0])
                 for call in printed.call_args_list if call.args])
        finally:
            release.set()
            self.assertEqual(await asyncio.to_thread(manager.shutdown, timeout_seconds=2), [])

    async def test_result_arriving_between_collect_and_status_check_is_not_lost(self):
        ns = self.namespace()
        calls = 0

        def inject(messages, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                messages.append({"role": "user", "content": "race result"})
                return 1
            return 0

        ns["SUBAGENT_TOOLS"].inject_subagent_results.side_effect = inject
        llm = AsyncScriptedClient(text_response("done?"), text_response("integrated"))
        await self.run_loop(ns, llm)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("race result", [m["content"] for m in llm.calls[1]["messages"]])

    async def test_round_limit_stops_before_another_model_call(self):
        ns = self.namespace()
        ns["AGENT_ROUND_BATCH"] = 1
        ns["confirm_more_rounds"] = AsyncMock(return_value=False)
        dispatcher = SimpleNamespace(execute_tool=AsyncMock(return_value="evidence"))
        llm = AsyncScriptedClient(tool_response(tool_call()))
        with self.assertRaises(ns["AgentRoundLimitError"]):
            await self.run_loop(ns, llm, dispatcher=dispatcher)
        self.assertEqual(len(llm.calls), 1)
        ns["confirm_more_rounds"].assert_awaited_once()
        ns["MEMORY_MANAGER"].extract_memories.assert_not_called()

    async def test_round_limit_approval_adds_another_batch(self):
        ns = self.namespace()
        ns["AGENT_ROUND_BATCH"] = 1
        ns["confirm_more_rounds"] = AsyncMock(return_value=True)
        llm = AsyncScriptedClient(tool_response(tool_call()), text_response("done"))
        await self.run_loop(ns, llm,
            dispatcher=SimpleNamespace(execute_tool=AsyncMock(return_value="evidence")))
        ns["confirm_more_rounds"].assert_awaited_once()
        self.assertEqual(len(llm.calls), 2)

    async def test_model_cancellation_does_not_extract_memory_or_append_response(self):
        ns = self.namespace()
        llm = AsyncScriptedClient(asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.run_loop(ns, llm)
        ns["MEMORY_MANAGER"].extract_memories.assert_not_called()
        ns["test_stop"].assert_not_called()

    async def test_confirm_more_rounds_awaits_input_before_normalizing(self):
        ns = self.namespace()
        for answers, expected in [([" YES "], True), (["invalid", "n"], False), ([EOFError()], False)]:
            reader = AsyncMock(side_effect=answers)
            pending = []

            def read(prompt):
                coroutine = reader(prompt)
                pending.append(coroutine)
                return coroutine

            try:
                self.assertIs(await ns["confirm_more_rounds"](60, SimpleNamespace(read=read)), expected)
                self.assertEqual(reader.await_count, len(answers))
            finally:
                # 即使实现错误地没有 await，也关闭测试创建的协程，避免遗留警告。
                for coroutine in pending:
                    coroutine.close()

    async def test_cleanup_orders_resources_and_is_idempotent(self):
        ns = self.namespace()
        order = []
        ns["SUBAGENTS"].shutdown.side_effect = lambda **kwargs: order.append("subagents") or []
        ns["SHELL"].stop_all_shell_processes.side_effect = lambda: order.append("shell")

        async def disconnect():
            await asyncio.sleep(0)
            order.append("mcp")

        ns["MCP_MANAGER"].disconnect_all_mcp.side_effect = disconnect
        ns["SUBAGENTS"].collect.side_effect = lambda: order.append("collect") or []
        ns["client"].close.side_effect = lambda: order.append("client")
        await ns["cleanup_program_async"]()
        await ns["cleanup_program_async"]()
        self.assertEqual(order, ["subagents", "shell", "mcp", "collect", "client"])
        ns["SUBAGENTS"].shutdown.assert_called_once_with(timeout_seconds=5.0)
        ns["MCP_MANAGER"].disconnect_all_mcp.assert_awaited_once()

    async def test_cleanup_continues_after_errors_and_keeps_client_for_live_subagent(self):
        ns = self.namespace()
        ns["SUBAGENTS"].shutdown.side_effect = RuntimeError("shutdown failed")
        ns["SHELL"].stop_all_shell_processes.side_effect = RuntimeError("shell failed")
        ns["MCP_MANAGER"].disconnect_all_mcp.side_effect = RuntimeError("mcp failed")
        ns["SUBAGENTS"].has_running.return_value = True
        await ns["cleanup_program_async"]()
        ns["SHELL"].stop_all_shell_processes.assert_called_once()
        ns["MCP_MANAGER"].disconnect_all_mcp.assert_awaited_once()
        ns["SUBAGENTS"].collect.assert_called_once()
        ns["client"].close.assert_not_called()

    def entry_namespace(self, *, reader, agent_loop=None):
        ns = self.namespace()
        events = []
        llm = SimpleNamespace()

        class ClientContext:
            async def __aenter__(self):
                events.append("llm-open")
                return llm

            async def __aexit__(self, *args):
                events.append("llm-close")

        async def cleanup():
            await asyncio.sleep(0)
            events.append("cleanup")

        signal = SimpleNamespace(SIGINT=2, SIGTERM=15,
            getsignal=Mock(side_effect=lambda signum: f"previous-{signum}"), signal=Mock())
        ns.update(
            AsyncConsole=Mock(return_value=SimpleNamespace(read=reader)),
            anthropic=SimpleNamespace(AsyncAnthropic=Mock(return_value=ClientContext())),
            os=SimpleNamespace(getenv=lambda name: None, getcwd=lambda: str(self.workdir)),
            signal=signal,
            patch_stdout=Mock(side_effect=lambda *, raw=False: nullcontext()),
            cleanup_program_async=AsyncMock(side_effect=cleanup),
            async_agent_loop=agent_loop or AsyncMock(),
        )
        return ns, events, llm

    async def test_entry_cleans_up_before_client_close_on_normal_and_error_exits(self):
        for answer in ("exit", EOFError(), "question"):
            with self.subTest(answer=repr(answer)):
                agent = AsyncMock(side_effect=RuntimeError("loop failed"))
                reader = AsyncMock(side_effect=[answer])
                ns, events, llm = self.entry_namespace(reader=reader, agent_loop=agent)
                if answer == "question":
                    with self.assertRaisesRegex(RuntimeError, "loop failed"):
                        await ns["async_main"]()
                    self.assertIs(agent.await_args.kwargs["llm"], llm)
                else:
                    await ns["async_main"]()
                    agent.assert_not_called()
                self.assertEqual(events, ["llm-open", "cleanup", "llm-close"])
                self.assertEqual(ns["signal"].signal.call_args_list[-2].args, (2, "previous-2"))
                self.assertEqual(ns["signal"].signal.call_args_list[-1].args, (15, "previous-15"))

    async def test_entry_signal_requests_cancellation_and_still_cleans_up(self):
        reading = asyncio.Event()

        async def read(prompt):
            reading.set()
            await asyncio.Event().wait()

        ns, events, _ = self.entry_namespace(reader=AsyncMock(side_effect=read))
        task = asyncio.create_task(ns["async_main"]())
        try:
            await asyncio.wait_for(reading.wait(), 2)
            handler = ns["signal"].signal.call_args_list[0].args[1]
            handler(2, None)  # 调用捕获的处理器，不向测试进程发送真实信号。
            handler(2, None)  # 重复退出请求应被忽略。
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(ns["_exit_requested"])
        ns["cleanup_program_async"].assert_awaited_once()
        self.assertEqual(events, ["llm-open", "cleanup", "llm-close"])
        self.assertEqual(ns["signal"].signal.call_args_list[-1].args, (15, "previous-15"))
