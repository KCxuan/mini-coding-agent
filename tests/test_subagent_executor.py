"""运行真实子循环，但模型回复、超时和故障均由测试控制。"""

import json
from unittest.mock import Mock, patch

from CodingAgent.hooks import HookRegistry
from CodingAgent.skill_loader import SkillLoader
from CodingAgent.subagent.executor import SubagentExecutor
from CodingAgent.subagent.results import format_subagent_result, subagent_evidence
from CodingAgent.subagent.state import SubagentState
from CodingAgent.tools.files import FileTools
from tests.helpers import (
    IsolatedTestCase, ScriptedClient, text_response, tool_call, tool_response,
)


class SubagentExecutorTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        (self.workdir / "sample.txt").write_text("verified content", encoding="utf-8")
        self.files = FileTools(self.workdir)
        self.loader = SkillLoader(self.workdir / "skills")

    def executor(self, client):
        return SubagentExecutor(
            client, "offline-model", workdir=self.workdir, system_prompt="read only",
            tools=[], files=self.files, skill_loader=self.loader, hooks=HookRegistry(),
        )

    def state(self, **kwargs):
        return SubagentState(task_id="task_12345678", prompt="Read sample.txt",
                             workdir=self.workdir, **kwargs)

    def test_read_then_finish_preserves_matched_evidence(self):
        client = ScriptedClient(tool_response(tool_call()), text_response())
        state = self.executor(client).execute_subagent(self.state())
        self.assertEqual(state.status, "completed")
        self.assertEqual(len(client.calls), 2)
        evidence = subagent_evidence(state)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["tool_use_id"], "call_1")
        self.assertEqual(evidence[0]["tool"], "read_file")
        self.assertIn("verified content", evidence[0]["output"])
        self.assertEqual(client.calls[1]["messages"][-1]["content"][0]["tool_use_id"], "call_1")

    def test_write_and_shell_handlers_never_execute(self):
        executor = self.executor(ScriptedClient())
        for name in ("write_file", "edit_file", "bash", "task", "mcp__x__y"):
            with self.subTest(tool=name):
                handler = Mock()
                result = executor.execute_subagent_tool(tool_call(name, {}), {name: handler})
                self.assertIn("只读模式禁止", result)
                handler.assert_not_called()

    def test_forbidden_model_request_returns_denial_to_next_turn(self):
        client = ScriptedClient(
            tool_response(tool_call("write_file", {"path": "new.txt", "content": "x"})),
            text_response(),
        )
        state = self.executor(client).execute_subagent(self.state())
        self.assertFalse((self.workdir / "new.txt").exists())
        self.assertIn("只读模式禁止", subagent_evidence(state)[0]["output"])
        self.assertIn("只读模式禁止", client.calls[1]["messages"][-1]["content"][0]["content"])

    def test_invalid_tool_arguments_do_not_call_handler(self):
        handler = Mock()
        result = self.executor(ScriptedClient()).execute_subagent_tool(
            tool_call(arguments=[]), {"read_file": handler})
        self.assertIn("参数必须是一个对象", result)
        handler.assert_not_called()

    def test_glob_rejects_parent_and_absolute_patterns(self):
        executor = self.executor(ScriptedClient())
        for pattern in ("../*.py", str(self.root / "*.py"), ""):
            with self.subTest(pattern=pattern):
                with self.assertRaises(ValueError):
                    executor.run_subagent_glob(pattern)

    def test_cancel_before_first_request_makes_no_model_call(self):
        client = ScriptedClient()
        state = self.state()
        state.cancel_event.set()
        result = self.executor(client).execute_subagent(state)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(client.calls, [])
        self.assertTrue(result.remaining)

    def test_cancel_after_tool_preserves_result_and_skips_next_tool(self):
        state = self.state()
        client = ScriptedClient(tool_response(
            tool_call(call_id="first"), tool_call(call_id="second")))
        original_read = self.files.run_read_file

        def read_and_cancel(**kwargs):
            output = original_read(**kwargs)
            state.cancel_event.set()
            return output

        with patch.object(self.files, "run_read_file", side_effect=read_and_cancel) as reader:
            self.executor(client).execute_subagent(state)
        self.assertEqual(state.status, "cancelled")
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(len(client.calls), 1)  # 取消后也不请求总结模型
        self.assertEqual([e["tool_use_id"] for e in subagent_evidence(state)], ["first"])

    def test_turn_exhaustion_hands_off_existing_evidence_without_tools(self):
        client = ScriptedClient(tool_response(tool_call()), text_response(
            '{"summary":"Read sample.txt", "remaining":"Need further analysis"}'))
        state = self.executor(client).execute_subagent(self.state(max_turns=1))
        self.assertEqual(state.status, "budget_exhausted")
        self.assertEqual(state.turns_used, 1)
        self.assertEqual(len(subagent_evidence(state)), 1)
        self.assertNotIn("tools", client.calls[-1])
        self.assertTrue(state.remaining)
        self.assertEqual([r["phase"] for r in state.response_log], ["work", "summary"])

    def test_output_truncation_does_not_execute_partial_tool_request(self):
        client = ScriptedClient(
            tool_response(tool_call(), stop_reason="max_tokens"), text_response())
        with patch.object(self.files, "run_read_file") as reader:
            state = self.executor(client).execute_subagent(self.state())
        self.assertEqual(state.status, "budget_exhausted")
        reader.assert_not_called()
        self.assertEqual(subagent_evidence(state), [])
        self.assertIn("token", state.error)

    def test_request_timeout_returns_timed_out_handoff(self):
        client = ScriptedClient(TimeoutError("simulated timeout"), text_response())
        state = self.executor(client).execute_subagent(self.state())
        self.assertEqual(state.status, "timed_out")
        self.assertIn("simulated timeout", state.error)
        self.assertNotIn("tools", client.calls[-1])

    def test_deadline_expiring_during_request_blocks_new_tools(self):
        clock = [0.0]

        def late_response():
            clock[0] = 2.0
            return tool_response(tool_call())

        client = ScriptedClient(late_response, text_response())
        with patch("CodingAgent.subagent.executor.time.monotonic", side_effect=lambda: clock[0]):
            with patch.object(self.files, "run_read_file") as reader:
                state = self.executor(client).execute_subagent(self.state(timeout_seconds=1))
        self.assertEqual(state.status, "timed_out")
        reader.assert_not_called()

    def test_model_and_summary_failure_keep_programmatic_handoff(self):
        client = ScriptedClient(RuntimeError("model unavailable"), RuntimeError("summary unavailable"))
        state = self.executor(client).execute_subagent(self.state())
        self.assertEqual(state.status, "failed")
        self.assertIn("model unavailable", state.error)
        self.assertTrue(state.summary)
        self.assertTrue(state.remaining)
        self.assertTrue(state.warnings)

    def test_truncated_summary_keeps_fallback_and_tool_evidence(self):
        client = ScriptedClient(tool_response(tool_call()), text_response(
            "partial summary", stop_reason="max_tokens"))
        state = self.executor(client).execute_subagent(self.state(max_turns=1))
        self.assertEqual(state.status, "budget_exhausted")
        self.assertNotEqual(state.summary, "partial summary")
        self.assertEqual(len(subagent_evidence(state)), 1)
        self.assertTrue(state.warnings)

    def test_non_json_final_answer_is_preserved_for_main_agent_review(self):
        client = ScriptedClient(text_response("Found a possible defect; needs verification."))
        state = self.executor(client).execute_subagent(self.state())
        result = json.loads(format_subagent_result(state))
        self.assertIn("possible defect", result["summary"])
        self.assertTrue(result["remaining"])
        self.assertEqual(result["task_id"], state.task_id)
        self.assertEqual(result["run_id"], state.run_id)
