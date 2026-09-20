"""后台通知与主循环集成；不导入会初始化真实客户端的 main 模块。"""

import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch

from CodingAgent.background import BackgroundManager
from CodingAgent.hooks import HookRegistry
from CodingAgent.tools.dispatcher import ToolDispatcher
from CodingAgent.tools.files import FileTools
from CodingAgent.tools.shell import ShellRunner
from CodingAgent.compact import ContextCompactor
from CodingAgent.context_budget import (
    CONTEXT_WINDOW_TOKENS, ContextBudgetError, estimate_request_tokens,
)
from tests.helpers import (
    IsolatedTestCase, ScriptedClient, load_main_functions,
    text_response, tool_call, tool_response,
)
import json

class BackgroundTests(IsolatedTestCase):
    def shell(self):
        # 只替换命令执行；归档和预览运行真实实现。
        shell = ShellRunner(self.workdir)
        shell.run_bash_process = Mock()
        return shell

    def start_and_finish(self, shell):
        manager = BackgroundManager(shell)
        threads = []
        real_thread = threading.Thread

        def capture_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            threads.append(thread)
            return thread

        with patch("CodingAgent.background.threading.Thread", side_effect=capture_thread):
            task_id = manager.start(tool_call("bash", {"command": "fake command"}))
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        return manager, task_id

    def test_success_result_is_injected_once(self):
        shell = self.shell()
        shell.run_bash_process.return_value = ("all checks passed", 0)
        manager, task_id = self.start_and_finish(shell)
        messages = [{"role": "assistant", "content": []}]
        self.assertEqual(manager.inject_background_results(messages), 1)
        self.assertEqual(manager.inject_background_results(messages), 0)
        text = messages[-1]["content"][0]["text"]
        self.assertIn(task_id, text)
        self.assertIn("<status>completed</status>", text)
        self.assertIn("all checks passed", text)
        self.assertFalse(manager.has_running())
        self.assertEqual(manager.results, {})

    def test_nonzero_exit_and_shell_exception_are_failures(self):
        for failure in (False, True):
            with self.subTest(exception=failure):
                shell = self.shell()
                if failure:
                    shell.run_bash_process.side_effect = RuntimeError("fake failure")
                else:
                    shell.run_bash_process.return_value = ("test failed", 2)
                manager, _ = self.start_and_finish(shell)
                notice = manager.collect()[0]
                self.assertIn("<status>failed</status>", notice)
                self.assertIn("fake failure" if failure else "code 2", notice)
                self.assertEqual(manager.collect(), [])

    def test_injection_preserves_existing_user_content(self):
        shell = self.shell()
        shell.run_bash_process.return_value = ("done", 0)
        manager, _ = self.start_and_finish(shell)
        messages = [{"role": "user", "content": "original request"}]
        manager.inject_background_results(messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"][0]["text"], "original request")

    def test_permission_denial_prevents_background_start_and_handler(self):
        hooks = HookRegistry()
        hooks.register("PreToolUse", lambda block: "Permission denied")
        background, handler = Mock(), Mock()
        result = ToolDispatcher(hooks, background).execute_tool(
            tool_call("bash", {"command": "fake", "run_in_background": True}),
            {"bash": handler},
        )
        self.assertEqual(result, "Permission denied")
        background.start.assert_not_called()
        handler.assert_not_called()

    def test_background_dispatch_returns_start_receipt_without_foreground_call(self):
        background, handler = Mock(), Mock()
        background.start.return_value = "bg_test"
        block = tool_call("bash", {"command": "fake", "run_in_background": True})
        result = ToolDispatcher(HookRegistry(), background).execute_tool(block, {"bash": handler})
        self.assertIn("bg_test", result)
        background.start.assert_called_once_with(block)
        handler.assert_not_called()

    def test_long_output_notification_preserves_full_text_and_head_tail(self):
        shell = self.shell()
        full = "HEAD<&>\r\n" + "中文 evidence\r\n" * 1000 + "TAIL_SENTINEL"
        shell.run_bash_process.return_value = (full, 0)
        manager, _ = self.start_and_finish(shell)
        notice = ET.fromstring(manager.collect()[0])
        preview = notice.findtext("summary")
        self.assertIn("HEAD<&>", preview)
        self.assertIn("TAIL_SENTINEL", preview)
        self.assertLessEqual(len(preview), 500)
        self.assertEqual(Path(notice.findtext("full_output")).read_bytes(), full.encode("utf-8"))
        self.assertEqual(notice.findtext("exit_code"), "0")
        self.assertEqual(manager.collect(), [])

    def test_archive_failure_delivers_full_output_without_fake_path(self):
        shell = self.shell()
        full = "COMPLETE_EVIDENCE" * 1000
        shell.run_bash_process.return_value = (full, 0)
        manager, _ = self.start_and_finish(shell)
        with patch("CodingAgent.tools.shell.Path.open", side_effect=OSError("disk full")):
            notice = ET.fromstring(manager.collect()[0])
        self.assertIsNone(notice.find("full_output"))
        self.assertIn(full, notice.findtext("summary"))
        self.assertIn("output archive failed", notice.findtext("summary"))
        self.assertEqual(manager.collect(), [])


class MainLoopTests(IsolatedTestCase):
    def namespace(self, client):
        background = Mock()
        background.inject_background_results.return_value = 0
        background.has_running.return_value = False
        subagents = Mock()
        subagents.has_running.return_value = False
        subagent_tools = Mock()
        subagent_tools.inject_subagent_results.return_value = 0
        memory = Mock()
        memory.load_memories.return_value = ""
        memory.extract_memories.return_value = 0
        compactor = Mock(spec=ContextCompactor)
        compactor.prepare.side_effect = lambda messages, request, **kwargs: messages
        compactor.reactive_compact.side_effect = lambda messages, request: messages
        mcp = Mock()
        mcp.assemble_tool_pool.side_effect = lambda **kwargs: ([], kwargs["builtin_handlers"])
        handlers = {"read_file": FileTools(self.workdir).run_read_file}
        hooks = HookRegistry()
        sleeps = []

        def bounded_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) > 5:
                raise AssertionError("Main loop kept waiting unexpectedly")

        namespace = {
            "client": client, "MODEL": "offline-model", "WORKDIR": self.workdir,
            "MAX_SUBAGENTS": 4, "MAX_REACTIVE_RETRIES": 1,
            "CONTEXT_WINDOW_TOKENS": CONTEXT_WINDOW_TOKENS,
            "ContextBudgetError": ContextBudgetError,
            "estimate_request_tokens": estimate_request_tokens,
            "input": Mock(side_effect=AssertionError("Unexpected interactive prompt")),
            "BACKGROUND": background, "SUBAGENTS": subagents,
            "SUBAGENT_TOOLS": subagent_tools, "MEMORY_MANAGER": memory,
            "MEMORY_STORE": Mock(), "SKILL_LOADER": Mock(), "COMPACTOR": compactor,
            "MCP_MANAGER": mcp, "HOOKS": hooks, "TOOLS": [], "TOOL_HANDLERS": handlers,
            "TOOL_DISPATCHER": ToolDispatcher(hooks, background),
            "build_system_prompt": lambda **kwargs: "offline prompt",
            "time": SimpleNamespace(sleep=bounded_sleep), "test_sleeps": sleeps,
            "PROJECT_INSTRUCTIONS": "",
            "json": json,
        }
        return load_main_functions(
            namespace, "agent_loop", "inject_async_results", "request_usable_response",
            "confirm_more_rounds", "IncompleteResponseError", "AgentRoundLimitError",
            "MAIN_OUTPUT_TOKENS", "MAIN_RETRY_OUTPUT_TOKENS", "MAX_RESPONSE_RETRIES",
            "AGENT_ROUND_BATCH",
        )

    def test_tool_result_id_and_content_reach_following_model_request(self):
        (self.workdir / "sample.txt").write_text("loop evidence", encoding="utf-8")
        client = ScriptedClient(tool_response(tool_call()), text_response("done"))
        ns = self.namespace(client)
        ns["agent_loop"]([{"role": "user", "content": "Read sample.txt"}], "Read sample.txt")
        self.assertEqual(len(client.calls), 2)
        result = client.calls[1]["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "call_1")
        self.assertIn("loop evidence", result["content"])

    def delivery(self, sequence):
        steps = deque(sequence)

        def inject(messages, *, tool_counts=None):
            if not steps:
                raise AssertionError("Unexpected additional result collection")
            count = steps.popleft()
            if count:
                messages.append({"role": "user", "content": "LATE_RESULT_SENTINEL"})
            return count

        return inject

    def test_late_background_result_reenters_model_without_busy_model_polling(self):
        client = ScriptedClient(text_response("waiting"), text_response("integrated result"))
        ns = self.namespace(client)
        ns["BACKGROUND"].inject_background_results.side_effect = self.delivery([0, 0, 0, 1, 0, 0, 0])
        ns["BACKGROUND"].has_running.side_effect = [True, True, False]
        messages = [{"role": "user", "content": "Run a background check"}]
        ns["agent_loop"](messages, "Run a background check")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(ns["test_sleeps"]), 2)
        second = client.calls[1]["messages"]
        self.assertEqual(sum(m["content"] == "LATE_RESULT_SENTINEL" for m in second), 1)
        ns["MEMORY_MANAGER"].extract_memories.assert_called_once()

    def test_result_finishing_between_collection_and_status_check_is_not_lost(self):
        client = ScriptedClient(text_response("done?"), text_response("integrated"))
        ns = self.namespace(client)
        ns["SUBAGENT_TOOLS"].inject_subagent_results.side_effect = self.delivery([0, 0, 1, 0, 0, 0])
        messages = [{"role": "user", "content": "Review"}]
        ns["agent_loop"](messages, "Review")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(ns["test_sleeps"], [])
        self.assertIn("LATE_RESULT_SENTINEL", [m["content"] for m in client.calls[1]["messages"]])

    def test_context_error_compacts_once_and_retries(self):
        client = ScriptedClient(RuntimeError("prompt_too_long"), text_response("done"))
        ns = self.namespace(client)
        ns["agent_loop"]([{"role": "user", "content": "Read"}], "Read")
        self.assertEqual(len(client.calls), 2)
        ns["COMPACTOR"].reactive_compact.assert_called_once()

    def test_repeated_context_error_stops_after_one_reactive_retry(self):
        client = ScriptedClient(RuntimeError("prompt_too_long"), RuntimeError("prompt_too_long"))
        ns = self.namespace(client)
        with self.assertRaisesRegex(RuntimeError, "prompt_too_long"):
            ns["agent_loop"]([{"role": "user", "content": "Read"}], "Read")
        self.assertEqual(len(client.calls), 2)
        ns["COMPACTOR"].reactive_compact.assert_called_once()

    def test_other_model_error_is_not_misclassified_as_context_error(self):
        ns = self.namespace(ScriptedClient(RuntimeError("authentication failed")))
        with self.assertRaisesRegex(RuntimeError, "authentication failed"):
            ns["agent_loop"]([{"role": "user", "content": "Read"}], "Read")
        ns["COMPACTOR"].reactive_compact.assert_not_called()

    def test_prepare_receives_actual_system_tools_and_retry_reserve(self):
        client = ScriptedClient(text_response("done"))
        ns = self.namespace(client)
        tools = [{"name": "read_file", "input_schema": {"type": "object"}}]
        ns["MCP_MANAGER"].assemble_tool_pool.side_effect = None
        ns["MCP_MANAGER"].assemble_tool_pool.return_value = (tools, ns["TOOL_HANDLERS"])
        ns["agent_loop"]([{"role": "user", "content": "Read"}], "Read")
        kwargs = ns["COMPACTOR"].prepare.call_args.kwargs
        self.assertEqual(kwargs["system_prompt"], client.calls[0]["system"])
        self.assertEqual(kwargs["tools"], client.calls[0]["tools"])
        self.assertEqual(kwargs["output_reserve"], ns["MAIN_RETRY_OUTPUT_TOKENS"])

    def test_truncated_tool_call_is_discarded_before_retry(self):
        partial = tool_response(tool_call("read_file", call_id="partial"), stop_reason="max_tokens")
        client = ScriptedClient(partial, text_response("done"))
        ns = self.namespace(client)
        handler = Mock(return_value="must not run")
        ns["TOOL_HANDLERS"]["read_file"] = handler
        messages = [{"role": "user", "content": "Read"}]
        ns["agent_loop"](messages, "Read")
        handler.assert_not_called()
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["messages"], client.calls[1]["messages"])
        self.assertEqual(client.calls[0]["max_tokens"], ns["MAIN_OUTPUT_TOKENS"])
        self.assertEqual(client.calls[1]["max_tokens"], ns["MAIN_RETRY_OUTPUT_TOKENS"])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["content"][0].text, "done")

    def test_repeated_truncation_raises_without_normal_finish(self):
        client = ScriptedClient(
            text_response("partial", stop_reason="max_tokens"),
            text_response("still partial", stop_reason="max_tokens"),
        )
        ns = self.namespace(client)
        messages = [{"role": "user", "content": "Read"}]
        with self.assertRaises(ns["IncompleteResponseError"]):
            ns["agent_loop"](messages, "Read")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(messages, [{"role": "user", "content": "Read"}])
        ns["MEMORY_MANAGER"].extract_memories.assert_not_called()

    def test_empty_or_thinking_only_response_retries_without_raising_output_limit(self):
        from tests.helpers import Block
        bad_responses = [
            text_response("   "),
            SimpleNamespace(content=[], stop_reason="end_turn"),
            SimpleNamespace(content=[Block(type="thinking", thinking="reasoning")],
                            stop_reason="end_turn"),
            text_response("no tool", stop_reason="tool_use"),
        ]
        for bad in bad_responses:
            with self.subTest(response=bad):
                client = ScriptedClient(bad, text_response("done"))
                ns = self.namespace(client)
                ns["agent_loop"]([{"role": "user", "content": "Read"}], "Read")
                self.assertEqual(len(client.calls), 2)
                self.assertEqual(client.calls[0]["max_tokens"], client.calls[1]["max_tokens"])

    def test_unknown_stop_reason_or_conflicting_tool_response_fails_immediately(self):
        for response in (text_response("text", stop_reason="unknown"),
                         tool_response(tool_call(), stop_reason="end_turn")):
            with self.subTest(response=response):
                client = ScriptedClient(response)
                ns = self.namespace(client)
                with self.assertRaises(ns["IncompleteResponseError"]):
                    ns["agent_loop"]([{"role": "user", "content": "Read"}], "Read")
                self.assertEqual(len(client.calls), 1)
                ns["MEMORY_MANAGER"].extract_memories.assert_not_called()

    def test_round_60_requires_permission_before_request_61(self):
        client = ScriptedClient(*(text_response("pending") for _ in range(60)))
        ns = self.namespace(client)
        self.assertEqual(ns["AGENT_ROUND_BATCH"], 60)
        ns["HOOKS"].register("Stop", lambda messages, count: "Continue checking")
        ns["input"] = Mock(return_value="n")
        with self.assertRaisesRegex(ns["AgentRoundLimitError"], "60"):
            ns["agent_loop"]([{"role": "user", "content": "Review"}], "Review")
        self.assertEqual(len(client.calls), 60)
        ns["input"].assert_called_once()
        ns["MEMORY_MANAGER"].extract_memories.assert_not_called()

    def test_accepting_extension_allows_120_rounds_then_asks_again(self):
        client = ScriptedClient(*(text_response("pending") for _ in range(120)))
        ns = self.namespace(client)
        ns["HOOKS"].register("Stop", lambda messages, count: "Continue checking")
        ns["input"] = Mock(side_effect=["yes", "no"])
        with self.assertRaisesRegex(ns["AgentRoundLimitError"], "120"):
            ns["agent_loop"]([{"role": "user", "content": "Review"}], "Review")
        self.assertEqual(len(client.calls), 120)
        self.assertEqual(ns["input"].call_count, 2)

    def test_finishing_on_round_60_does_not_ask_for_extension(self):
        client = ScriptedClient(*(text_response("pending") for _ in range(60)))
        ns = self.namespace(client)
        ns["HOOKS"].register("Stop", Mock(side_effect=["Continue"] * 59 + [None]))
        ns["agent_loop"]([{"role": "user", "content": "Review"}], "Review")
        self.assertEqual(len(client.calls), 60)
        ns["input"].assert_not_called()
        ns["MEMORY_MANAGER"].extract_memories.assert_called_once()

    def test_confirmation_reprompts_invalid_answer_and_declines_eof(self):
        ns = self.namespace(ScriptedClient())
        ns["input"] = Mock(side_effect=["perhaps", " Y "])
        self.assertTrue(ns["confirm_more_rounds"](60))
        self.assertEqual(ns["input"].call_count, 2)
        ns["input"] = Mock(side_effect=EOFError)
        self.assertFalse(ns["confirm_more_rounds"](60))

    def test_tool_count_survives_real_compaction_and_resets_for_next_request(self):
        client = ScriptedClient(
            *(tool_response(tool_call(call_id=f"read_{i}")) for i in range(8)),
            tool_response(tool_call("compact", {}, call_id="compact_1")),
            text_response("done"), text_response("next task done"),
        )
        ns = self.namespace(client)
        summary_client = ScriptedClient(text_response("Read files; all evidence saved."))
        ns["COMPACTOR"] = ContextCompactor(
            summary_client, "offline-model", self.workdir / "transcripts",
            self.workdir / "outputs",
        )
        ns["TOOL_HANDLERS"]["read_file"] = Mock(return_value="evidence" * 300)
        ns["TOOL_HANDLERS"]["compact"] = Mock(return_value="Compact requested")
        counts = []
        ns["HOOKS"].register("Stop", lambda messages, count: counts.append(count))
        messages = [{"role": "user", "content": "Read"}]
        ns["agent_loop"](messages, "Read")
        self.assertEqual(len(summary_client.calls), 1)
        self.assertEqual(counts, [9])
        remaining_calls = sum(
            getattr(block, "type", None) == "tool_use"
            for message in messages if isinstance(message["content"], list)
            for block in message["content"]
        )
        self.assertLess(remaining_calls, counts[0])
        messages.append({"role": "user", "content": "Next task"})
        ns["agent_loop"](messages, "Next task")
        self.assertEqual(counts, [9, 0])

    def test_context_budget_failure_stops_before_model_call(self):
        client = ScriptedClient()
        ns = self.namespace(client)
        ns["COMPACTOR"].prepare.side_effect = ContextBudgetError("request cannot fit")
        with self.assertRaises(ContextBudgetError):
            ns["agent_loop"]([{"role": "user", "content": "Read"}], "Read")
        self.assertEqual(client.calls, [])
        ns["MEMORY_MANAGER"].extract_memories.assert_not_called()
    
    def test_subagent_counts_are_deduplicated_and_scoped(self):
        from CodingAgent.subagent.state import SubagentState
        from CodingAgent.subagent.results import format_subagent_result
        from CodingAgent.tools.adapters import SubagentTools

        states = []

        for status, count in (
            ("completed", 4),
            ("failed", 2),
            ("cancelled", 3),
        ):
            state = SubagentState(
                task_id="task_check",
                prompt="Check",
                workdir=self.workdir,
            )
            state.status = status
            state.tool_call_count = count
            states.append(state)

            result = json.loads(format_subagent_result(state))
            self.assertEqual(result["tool_call_count"], count)

        manager = Mock()
        manager.collect.return_value = states
        tools = SubagentTools(manager)

        counts = {
            state.run_id: 0
            for state in states
        }

        # 模拟重复投递，同一运行不能累计两次。
        tools.inject_subagent_results([], tool_counts=counts)
        tools.inject_subagent_results([], tool_counts=counts)

        self.assertEqual(sum(counts.values()), 9)

        # 未登记的运行，不计入本轮。
        unrelated = SubagentState(
            task_id="task_other",
            prompt="Other",
            workdir=self.workdir,
        )
        unrelated.tool_call_count = 100
        manager.collect.return_value = [unrelated]

        tools.inject_subagent_results([], tool_counts=counts)

        self.assertNotIn(unrelated.run_id, counts)
        self.assertEqual(sum(counts.values()), 9)
