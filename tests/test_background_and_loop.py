"""后台通知与主循环集成；不导入会初始化真实客户端的 main 模块。"""

import threading
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch

from CodingAgent.background import BackgroundManager
from CodingAgent.hooks import HookRegistry
from CodingAgent.tools.dispatcher import ToolDispatcher
from CodingAgent.tools.files import FileTools
from tests.helpers import (
    IsolatedTestCase, ScriptedClient, load_main_functions,
    text_response, tool_call, tool_response,
)


class BackgroundTests(IsolatedTestCase):
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
        shell = Mock()
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
                shell = Mock()
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
        shell = Mock()
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
        compactor = Mock()
        compactor.prepare.side_effect = lambda messages, request: messages
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
            "BACKGROUND": background, "SUBAGENTS": subagents,
            "SUBAGENT_TOOLS": subagent_tools, "MEMORY_MANAGER": memory,
            "MEMORY_STORE": Mock(), "SKILL_LOADER": Mock(), "COMPACTOR": compactor,
            "MCP_MANAGER": mcp, "HOOKS": hooks, "TOOLS": [], "TOOL_HANDLERS": handlers,
            "TOOL_DISPATCHER": ToolDispatcher(hooks, background),
            "build_system_prompt": lambda **kwargs: "offline prompt",
            "time": SimpleNamespace(sleep=bounded_sleep), "test_sleeps": sleeps,
        }
        return load_main_functions(namespace, "agent_loop", "inject_async_results")

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

        def inject(messages):
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
