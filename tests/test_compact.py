"""压缩必须保留可追溯原文，并维护工具调用/结果配对。"""

import copy
from collections import deque
import json
from pathlib import Path
from unittest.mock import patch

from CodingAgent.compact import ContextCompactor
from CodingAgent.context_budget import ContextBudgetError, estimate_request_tokens
from tests.helpers import IsolatedTestCase, ScriptedClient, text_response, tool_call, tool_response


def exchange(number, output="x" * 300):
    call_id = f"call_{number}"
    return [
        {"role": "assistant", "content": [{"type": "tool_use", "id": call_id,
          "name": "read_file", "input": {"path": "sample.txt"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id,
          "content": output}]},
    ]


class CompactTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.client = ScriptedClient(text_response("Goal and constraints preserved."))
        self.compactor = ContextCompactor(
            self.client, "offline-model",
            self.workdir / "transcripts", self.workdir / "outputs",
            context_window_tokens=1_000_000,
        )

    def assert_tool_pairs(self, messages):
        pending = set()
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                self.assertEqual(pending, set(), "Archive split a tool exchange")
                continue
            for block in content:
                if block.get("type") == "tool_use":
                    pending.add(block["id"])
                elif block.get("type") == "tool_result":
                    self.assertIn(block["tool_use_id"], pending)
                    pending.remove(block["tool_use_id"])
        self.assertEqual(pending, set())

    def test_large_output_preview_points_to_exact_full_content(self):
        full = "头" + "x" * 40000 + "END_SENTINEL"
        preview = self.compactor.persist_large_output("call_a", full)
        path = self.compactor.persisted_output_path(preview)
        self.assertIsNotNone(path)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), full)
        self.assertLess(len(preview), len(full))
        self.assertIn("END_SENTINEL", preview)
        self.assertIn("头", preview)

    def test_repeated_preview_does_not_overwrite_original_with_preview(self):
        full = "original" * 6000
        first = self.compactor.persisted_preview("call_a", full)
        second = self.compactor.persisted_preview("call_a", first)
        path = self.compactor.persisted_output_path(second)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), full)

    def test_fake_persisted_marker_outside_output_dir_is_not_trusted(self):
        outside = self.root / "outside.txt"
        outside.write_text("private content", encoding="utf-8")
        marker = f"<persisted-output>\nFull output: {outside}\nPreview:\ntext\n</persisted-output>"
        self.assertIsNone(self.compactor.persisted_output_path(marker))
        self.assertNotIn("private content", self.compactor.persisted_preview("call_a", marker))

    def test_output_filename_is_confined_even_for_malicious_tool_id(self):
        path = self.compactor.save_output("../../outside", "saved")
        self.assertTrue(path.resolve().is_relative_to(self.compactor.tool_results_dir.resolve()))
        self.assertEqual(path.read_text(encoding="utf-8"), "saved")

    def test_micro_compact_keeps_unseen_and_recent_results(self):
        messages = [{"role": "user", "content": "Read files"}]
        for number in range(6):
            messages.extend(exchange(number, "x" * 4000))
        original = copy.deepcopy(messages)
        result = self.compactor.micro_compact(messages)
        self.assertNotEqual(result[2]["content"][0]["content"], original[2]["content"][0]["content"])
        path = self.compactor.persisted_output_path(result[2]["content"][0]["content"])
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "x" * 4000)
        # 前五项已被模型看到；保留其中最近三项，以及第六项尚未消费的结果。
        for number in (2, 3, 4, 5):
            self.assertEqual(result[2 + number * 2], original[2 + number * 2])
        self.assert_tool_pairs(result)

    def test_summary_preserves_recent_pairs_and_full_transcript(self):
        messages = [{"role": "user", "content": "Read files"}]
        for number in range(30):
            messages.extend(exchange(number))
        before = copy.deepcopy(messages)
        result = self.compactor.compact_history(messages, "Read files")
        self.assertEqual(result[1:], before[-6:])
        summary_input = json.loads(self.client.calls[0]["messages"][0]["content"])
        self.assertEqual(summary_input, before[:-6])
        self.assertLess(len(result), len(messages))
        self.assert_tool_pairs(result)
        files = list(self.compactor.transcript_dir.glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        archived = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual(archived, before)

    def test_batch_budget_saves_large_result_without_changing_call_id(self):
        messages = exchange(1, "z" * 40000)
        result = self.compactor.tool_result_budget(messages, max_chars=10000)
        block = result[-1]["content"][0]
        self.assertEqual(block["tool_use_id"], "call_1")
        self.assertIsNotNone(self.compactor.persisted_output_path(block["content"]))
        self.assert_tool_pairs(result)

    def test_explicit_summary_preserves_current_request_and_archive(self):
        messages = [{"role": "user", "content": "Original goal"}]
        for number in range(8):
            messages.extend(exchange(number))
        result = self.compactor.compact_history(messages, "Do not modify files")
        self.assertIn("Do not modify files", result[0]["content"])
        self.assertIn("Goal and constraints preserved.", result[0]["content"])
        self.assertEqual(len(list(self.compactor.transcript_dir.glob("*.jsonl"))), 1)
        self.assertEqual(len(self.client.calls), 1)

    def test_summary_request_uses_default_output_budget_and_timeout(self):
        # 保留业务默认配置，检查真正传给模型客户端的参数。
        summary = self.compactor.summarize_history(
            [{"role": "user", "content": "Preserve the project constraints."}]
        )
        self.assertEqual(summary, "Goal and constraints preserved.")
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0]["max_tokens"], 25000)
        self.assertEqual(self.client.calls[0]["timeout"], 1200)

    def test_reactive_compact_keeps_recent_tool_pairs(self):
        messages = [{"role": "user", "content": "Original goal"}]
        for number in range(8):
            messages.extend(exchange(number))
        result = self.compactor.reactive_compact(messages, "Read only")
        self.assertIn("Read only", result[0]["content"])
        self.assert_tool_pairs(result[1:])
        self.assertEqual(result[-1], messages[-1])

    def history(self, count=12, output="x" * 10000):
        messages = [{"role": "user", "content": "Original goal"}]
        for number in range(count):
            messages.extend(exchange(number, output))
        return messages

    def prepare(self, messages, *, system="system", reserve=500):
        return self.compactor.prepare(
            messages, "Current request", system_prompt=system,
            tools=[], output_reserve=reserve,
        )

    def test_split_moves_back_before_sdk_tool_call(self):
        messages = self.history(count=4, output="result")
        # 九条消息按最近五条切，会切到 call_1 的调用与结果之间。
        messages[3]["content"] = [tool_call(call_id="call_1")]
        old, recent = self.compactor.split_recent_history(messages)
        self.assertEqual(old, messages[:3])
        self.assertEqual(recent, messages[3:])
        self.assertEqual(len(recent), 6)
        self.assertEqual(recent[0]["content"][0].id, recent[1]["content"][0]["tool_use_id"])

    def test_short_history_is_not_summarized(self):
        messages = exchange(1)
        self.assertIs(self.compactor.compact_history(messages, "Read"), messages)
        self.assertEqual(self.client.calls, [])
        self.assertFalse(self.compactor.transcript_dir.exists())

    def test_multiple_compactions_include_previous_summary(self):
        self.client.responses.append(text_response("Updated state."))
        first = self.compactor.compact_history(self.history(), "Keep read-only")
        messages = copy.deepcopy(first)
        for number in range(20, 28):
            messages.extend(exchange(number, "new evidence" * 100))
        second = self.compactor.compact_history(messages, "Keep read-only")
        self.assertEqual(len(self.client.calls), 2)
        self.assertIn("Goal and constraints preserved.",
                      self.client.calls[1]["messages"][0]["content"])
        self.assertIn("Updated state.", second[0]["content"])
        self.assertIn("Keep read-only", second[0]["content"])
        self.assertEqual(second[1:], messages[-6:])
        self.assert_tool_pairs(second)

    def test_bad_summary_never_replaces_original_history(self):
        failures = [
            text_response("partial", stop_reason="max_tokens"),
            text_response("   "),
            tool_response(tool_call(), stop_reason="end_turn"),
            RuntimeError("model unavailable"),
        ]
        for response in failures:
            with self.subTest(response=response):
                self.client.responses = deque([response])
                messages = self.history()
                before = copy.deepcopy(messages)
                with self.assertRaises(RuntimeError):
                    self.compactor.compact_history(messages, "Read")
                self.assertEqual(messages, before)

    def test_summary_that_grows_history_is_rejected(self):
        self.client.responses.clear()
        self.client.responses.append(text_response("summary" * 20000))
        messages = self.history(count=4, output="x" * 300)
        before = copy.deepcopy(messages)
        with self.assertRaisesRegex(RuntimeError, "没有缩短"):
            self.compactor.compact_history(messages, "Read")
        self.assertEqual(messages, before)

    def test_summary_input_keeps_middle_beyond_old_character_limit(self):
        messages = [{"role": "user", "content":
                     "中" * 50000 + "MIDDLE_SENTINEL" + "文" * 50000}]
        self.assertEqual(json.loads(self.compactor.summary_input(messages)), messages)

    def test_oversized_summary_request_stops_before_model_call(self):
        messages = self.history(count=8, output="中" * 10000)
        before = copy.deepcopy(messages)
        self.compactor.context_window_tokens = 5000
        with self.assertRaisesRegex(ContextBudgetError, "摘要请求自身超过预算"):
            self.compactor.compact_history(messages, "Read")
        self.assertEqual(self.client.calls, [])
        self.assertEqual(messages, before)

    def test_prepare_under_budget_does_not_summarize_or_mutate_original(self):
        messages = self.history(count=4)
        before = copy.deepcopy(messages)
        result = self.prepare(messages)
        self.assertEqual(result, before)
        self.assertIsNot(result, messages)
        self.assertEqual(self.client.calls, [])
        result[-1]["content"][0]["content"] = "changed copy"
        self.assertEqual(messages, before)

    def test_prepare_budget_includes_output_reserve_at_trigger_boundary(self):
        messages = [{"role": "user", "content": "Read"}]
        input_tokens = estimate_request_tokens("system", messages, [])
        self.compactor.context_window_tokens = 10000
        self.assertEqual(self.prepare(messages, reserve=9000 - input_tokens - 1), messages)
        # 刚好到 90% 就必须压缩；这里没有旧历史可缩，所以明确停止。
        with self.assertRaises(ContextBudgetError):
            self.prepare(messages, reserve=9000 - input_tokens)
        self.assertEqual(self.client.calls, [])

    def test_prepare_micro_compaction_suffices_without_model_call(self):
        messages = self.history()
        before = copy.deepcopy(messages)
        self.compactor.context_window_tokens = 20000
        result = self.prepare(messages)
        self.assertEqual(self.client.calls, [])
        self.assertLess(estimate_request_tokens("system", result, []) + 500, 18000)
        self.assertNotEqual(result[2], before[2])
        self.assertEqual(result[-8:], before[-8:])  # 三个已消费结果及一个未消费结果
        self.assertEqual(messages, before)
        self.assert_tool_pairs(result)

    def test_prepare_summarizes_after_old_result_previews_are_insufficient(self):
        messages = self.history()
        before = copy.deepcopy(messages)
        self.compactor.context_window_tokens = 14000
        # 小窗口搭配小摘要额度，确保测试能走到模型压缩这一步。
        self.compactor.SUMMARY_OUTPUT_TOKENS = 2000
        result = self.prepare(messages)
        self.assertEqual(len(self.client.calls), 1)
        self.assertLess(estimate_request_tokens("system", result, []) + 500, 12600)
        self.assertEqual(result[1:], before[-6:])
        self.assertEqual(messages, before)
        self.assertNotIn("tools", self.client.calls[0])
        self.assert_tool_pairs(result)

    def test_failed_prepare_preserves_history_even_after_tool_shortening(self):
        messages = self.history()
        before = copy.deepcopy(messages)
        self.compactor.context_window_tokens = 20000
        # 让摘要请求本身装得下，再验证压缩后的完整请求仍超预算。
        self.compactor.SUMMARY_OUTPUT_TOKENS = 2000
        with self.assertRaisesRegex(ContextBudgetError, "压缩后预计请求仍超过预算"):
            self.prepare(messages, system="!" * 20000)
        self.assertEqual(len(self.client.calls), 1)
        self.assertTrue(list(self.compactor.tool_results_dir.glob("*.txt")))
        self.assertEqual(messages, before)

    def test_preview_preserves_unicode_newlines_and_scans_to_true_tail(self):
        full = "HEAD\r\n中文😀" + "中\r\n" * 30000 + "TAIL_SENTINEL"
        preview = self.compactor.persisted_preview("same_id", full, preview_chars=101)
        path = Path(self.compactor.persisted_output_path(preview))
        self.assertEqual(path.read_bytes(), full.encode("utf-8"))
        self.assertIn(full[:51], preview)
        self.assertIn(full[-50:], preview)
        second = self.compactor.persisted_preview("same_id", preview, preview_chars=50)
        self.assertEqual(self.compactor.persisted_output_path(second), str(path))
        self.assertEqual(path.read_bytes(), full.encode("utf-8"))

    def test_save_failure_keeps_complete_tool_output(self):
        full = "important evidence" * 1000
        with patch.object(self.compactor, "save_output", side_effect=OSError("disk full")):
            self.assertEqual(self.compactor.persisted_preview("id", full), full)
