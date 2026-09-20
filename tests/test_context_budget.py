"""完整请求的本地粗估；不声称等于服务端 tokenizer 的真实 token 数。"""

import json

from anthropic.types import TextBlock, ThinkingBlock, ToolUseBlock

from CodingAgent.context_budget import dump_json, estimate_request_tokens
from tests.helpers import IsolatedTestCase


class ContextBudgetTests(IsolatedTestCase):
    def test_sdk_blocks_serialize_as_structured_json(self):
        blocks = [
            TextBlock(type="text", text="中文正文"),
            ThinkingBlock(type="thinking", thinking="reasoning", signature="signature"),
            ToolUseBlock(type="tool_use", id="call_1", name="read_file", input={"path": "a.py"}),
        ]
        encoded = dump_json([{"role": "assistant", "content": blocks}])
        decoded = json.loads(encoded)[0]["content"]
        self.assertIn("中文正文", encoded)
        self.assertEqual(decoded[0]["text"], "中文正文")
        self.assertEqual(decoded[1]["thinking"], "reasoning")
        self.assertEqual(decoded[1]["signature"], "signature")
        self.assertEqual(decoded[2]["input"], {"path": "a.py"})

    def test_system_tool_schema_and_tool_arguments_contribute_to_budget(self):
        messages = [{"role": "user", "content": "Read"}]
        baseline = estimate_request_tokens("", messages, [])
        self.assertGreater(estimate_request_tokens("system " * 1000, messages, []), baseline)
        tools = [{"name": "read_file", "description": "description " * 1000,
                  "input_schema": {"type": "object"}}]
        self.assertGreater(estimate_request_tokens("", messages, tools), baseline)
        short = [{"role": "assistant", "content": [
            ToolUseBlock(type="tool_use", id="id", name="write_file", input={"content": "x"})
        ]}]
        long = [{"role": "assistant", "content": [
            ToolUseBlock(type="tool_use", id="id", name="write_file", input={"content": "x" * 10000})
        ]}]
        self.assertGreater(estimate_request_tokens("", long, []),
                           estimate_request_tokens("", short, []))

    def test_thinking_and_tool_results_sent_in_history_are_counted(self):
        for kind in ("thinking", "tool_result"):
            with self.subTest(kind=kind):
                def messages(text):
                    block = ({"type": kind, "thinking": text, "signature": "sig"}
                             if kind == "thinking" else
                             {"type": kind, "tool_use_id": "id", "content": text})
                    return [{"role": "assistant" if kind == "thinking" else "user",
                             "content": [block]}]
                self.assertGreater(estimate_request_tokens("", messages("中" * 10000), []),
                                   estimate_request_tokens("", messages(""), []))

    def test_unsupported_objects_fail_instead_of_silently_losing_structure(self):
        with self.assertRaises(TypeError):
            dump_json({"content": object()})
