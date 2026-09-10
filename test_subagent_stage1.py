"""Subagent 离线回归测试（保留第一阶段入口，覆盖当前只读子循环）。

运行：
    python -B test_subagent_stage1.py
    python -B test_subagent_stage1.py SubagentStageOneTests.test_turn_budget

测试当前 main.py 中的真实子循环、收尾、证据提取和工具分发代码，
模型、任务读取和 shell 进程使用模拟对象；分页读取测试只使用临时文件。
不读取 .env、不调用 API、不运行命令、不修改看板或业务文件。

使用 AST 只加载指定定义，避免 import main 导致客户端初始化等副作用。
因此本脚本不验证整个 CLI 的启动、SDK 兼容性、提示词遵守率或真实进程终止。
未来拆分模块后，可以改为直接导入没有初始化副作用的 subagent 模块。
"""

from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
import threading
import types
import unittest
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4


SOURCE = Path(__file__).resolve().with_name("main.py")
DEFINITIONS = {
    "SubagentState", "subagent_evidence", "apply_subagent_summary",
    "execute_subagent", "finalize_subagent", "run_subagent",
    "execute_tool", "extract_text", "trigger_hooks", "_run_bash_process",
    "record_subagent_response", "execute_subagent_tool",
    "SubagentManager", "format_subagent_result", "safe_path",
    "run_read_file", "run_glob", "run_subagent_glob",
}
CONSTANTS = {
    "SUB_SYSTEM_PROMPT", "SUB_TOOLS", "TASK_TOOL",
    "BASE_TOOLS", "SUB_READONLY_TOOL_NAMES",
}


class FakeClock:
    """手动推进时间，让超时测试瞬间完成。"""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeAPITimeoutError(Exception):
    pass


class FakeBlock:
    """模拟代码实际用到的 SDK 响应块接口。"""

    def __init__(self, block_type, **fields):
        self.type = block_type
        self.__dict__.update(fields)

    def model_dump(self, **kwargs):
        return copy.deepcopy(self.__dict__)


def final_response(summary="finished", remaining=""):
    text = json.dumps({"summary": summary, "remaining": remaining})
    return types.SimpleNamespace(content=[FakeBlock("text", text=text)])


def tool_call(name="read_file", call_id="call_1", **arguments):
    return FakeBlock("tool_use", id=call_id, name=name, input=arguments)


def tool_response(*calls):
    return types.SimpleNamespace(content=list(calls))


class FakeClient:
    """work 中预设每轮回答；summary 中预设收尾回答或异常。"""

    def __init__(self):
        self.work = []
        self.summary = [final_response("partial findings", "needs verification")]
        self.requests = []

    def with_options(self, **options):
        def create(**request):
            self.requests.append({
                "options": copy.deepcopy(options),
                "request": copy.deepcopy(request),
            })
            queue = self.work if "tools" in request else self.summary
            if not queue:
                raise AssertionError("模型请求次数超出预设场景")
            action = queue.pop(0)
            if isinstance(action, Exception):
                raise action
            return action() if callable(action) else action

        return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


def load_runtime(clock, client):
    """加载 main.py 的指定源码定义，不执行文件其余顶层代码。"""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"), filename=str(SOURCE))
    selected = []
    counts = dict.fromkeys(DEFINITIONS | CONSTANTS, 0)

    for node in tree.body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            if node.name in DEFINITIONS:
                name = node.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in CONSTANTS:
                name = target.id
        if name is not None:
            selected.append(node)
            counts[name] += 1

    invalid = {name: count for name, count in counts.items() if count != 1}
    if invalid:
        raise RuntimeError(f"需要且只能有一份下列源码定义（名称: 实际数量）：{invalid}")

    # dataclass 需要能通过 __module__ 找到定义所属模块。
    module_name = "_subagent_stage1_under_test"
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module
    process = Mock(name="fake_process")
    process.returncode = 0
    process.communicate.return_value = ("simulated output", "")
    read_file = Mock(name="read_file", return_value="example file content")

    def unexpected_operation(*args, **kwargs):
        raise AssertionError("本场景不允许执行此工具")

    module.__dict__.update({
        "dataclass": dataclass, "field": field, "Path": Path,
        "uuid4": uuid4, "json": json, "time": clock,
        "WORKDIR": SOURCE.parent, "MODEL": "offline-fake-model",
        "client": client,
        "threading": threading,
        "SKILL_LOADER": types.SimpleNamespace(load=unexpected_operation),
        "anthropic": types.SimpleNamespace(APITimeoutError=FakeAPITimeoutError),
        "HOOKS": {name: [] for name in ("PreToolUse", "PostToolUse", "Stop")},
        "BASE_TOOL_HANDLERS": {
            "read_file": read_file,
            "bash": unexpected_operation,
            "write_file": unexpected_operation,
            "edit_file": unexpected_operation,
            "glob": unexpected_operation,
            "load_skill": unexpected_operation,
        },
        "load_task": Mock(return_value=types.SimpleNamespace(
            status="in_progress", owner="agent"
        )),
        # 加载真实 _run_bash_process，但用假 Popen 保证不启动进程。
        "subprocess": types.SimpleNamespace(
            Popen=Mock(return_value=process),
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
        "_popen_kwargs": Mock(return_value={}),
        "_stop_process_group": Mock(),
        "_shell_processes": set(),
        "_shell_process_lock": threading.Lock(),
        "print": Mock(),  # 测试结果只由 unittest 打印。
    })
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    isolated = ast.fix_missing_locations(ast.Module(body=[future, *selected], type_ignores=[]))
    exec(compile(isolated, str(SOURCE), "exec"), module.__dict__)
    module.real_read_file = module.run_read_file
    module.run_read_file = read_file
    module.SUBAGENTS = module.SubagentManager()
    return module, read_file, process


class SubagentStageOneTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.client = FakeClient()
        self.agent, self.read_file, self.process = load_runtime(self.clock, self.client)

    def state(self, **options):
        return self.agent.SubagentState(
            task_id="task_12345678", prompt="offline test", **options
        )

    def run_state(self, **options):
        return self.agent.execute_subagent(self.state(**options))

    def evidence(self, state):
        return self.agent.subagent_evidence(state)

    def assert_summary_request(self):
        latest = self.client.requests[-1]
        self.assertNotIn("tools", latest["request"])
        self.assertEqual(latest["options"]["max_retries"], 0)
        self.assertEqual(latest["options"]["timeout"], 20)

    def test_normal_completion(self):
        """正常完成：返回同一状态对象，不额外调用总结模型。"""
        self.client.work = [final_response("verified")]
        original = self.state()
        result = self.agent.execute_subagent(original)
        self.assertIs(result, original)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.summary, "verified")
        self.assertEqual(result.remaining, "")
        self.assertEqual(len(self.client.requests), 1)

    def test_turn_budget(self):
        """轮数耗尽：最后一次工具结果进入收尾请求，状态不冒充成功。"""
        self.client.work = [tool_response(tool_call(path="example.py"))]
        result = self.run_state(max_turns=1)
        self.assertEqual(result.status, "budget_exhausted")
        self.assertEqual(result.turns_used, 1)
        self.assertEqual(len(self.evidence(result)), 1)
        self.assertEqual(result.summary, "partial findings")
        self.assert_summary_request()
        self.assertIn("example file content", self.client.requests[-1]["request"]["messages"][0]["content"])

    def test_tool_error_then_recovery(self):
        """工具报错后，真实 execute_subagent_tool 把错误交回模型，允许继续。"""
        self.read_file.side_effect = [FileNotFoundError("missing file"), "recovered"]
        self.client.work = [
            tool_response(tool_call(path="missing.py")),
            tool_response(tool_call(call_id="call_2", path="exists.py")),
            final_response(),
        ]
        result = self.run_state()
        self.assertEqual(result.status, "completed")
        records = self.evidence(result)
        self.assertEqual(len(records), 2)
        self.assertIn("missing file", records[0]["output"])
        self.assertEqual(records[1]["output"], "recovered")

    def test_model_failure_keeps_earlier_evidence(self):
        """模型请求失败时仍返回 failed，并保留之前的工具结果。"""
        self.client.work = [
            tool_response(tool_call(path="example.py")), RuntimeError("model offline")
        ]
        result = self.run_state()
        self.assertEqual(result.status, "failed")
        self.assertIn("model offline", result.error)
        self.assertEqual(len(self.evidence(result)), 1)
        self.assert_summary_request()

    def test_summary_failure_keeps_fallback(self):
        """总结也失败：保留预算耗尽状态、基本摘要和证据。"""
        self.client.work = [tool_response(tool_call(path="example.py"))]
        self.client.summary = [RuntimeError("summary offline")]
        result = self.run_state(max_turns=1)
        self.assertEqual(result.status, "budget_exhausted")
        self.assertTrue(result.summary)
        self.assertTrue(result.remaining)
        self.assertTrue(any("summary offline" in item for item in result.warnings))
        self.assertEqual(len(self.evidence(result)), 1)

    def test_api_timeout(self):
        """模拟 SDK 抛出超时异常，触发 timed_out 和收尾。"""
        self.client.work = [FakeAPITimeoutError("request timeout")]
        result = self.run_state()
        self.assertEqual(result.status, "timed_out")
        self.assertIn("request timeout", result.error)
        self.read_file.assert_not_called()
        self.assert_summary_request()

    def test_late_response_cannot_start_tool(self):
        """模型返回时已过期，响应里的工具请求不能再执行。"""
        def late_response():
            self.clock.advance(601)
            return tool_response(tool_call(path="must_not_run.py"))
        self.client.work = [late_response]
        result = self.run_state()
        self.assertEqual(result.status, "timed_out")
        self.read_file.assert_not_called()
        self.assertEqual(self.evidence(result), [])

    def test_timeout_mid_batch_preserves_first_result(self):
        """同一批工具中第一个用完时间，保留其结果并跳过第二个。"""
        def slow_read(**kwargs):
            self.clock.advance(601)
            return "first result survives"
        self.read_file.side_effect = slow_read
        self.client.work = [tool_response(
            tool_call(path="first.py"), tool_call(call_id="call_2", path="second.py")
        )]
        result = self.run_state()
        self.assertEqual(result.status, "timed_out")
        self.read_file.assert_called_once_with(path="first.py")
        self.assertEqual(self.evidence(result)[0]["output"], "first result survives")
        self.assertEqual(len(self.evidence(result)), 1)

    def test_readonly_tools_do_not_enter_permission_hooks(self):
        """只读入口无需询问，主 Agent 的审批钩子不会被子 Agent 调用。"""
        permission = Mock(side_effect=AssertionError("must not ask"))
        self.agent.HOOKS["PreToolUse"].append(permission)
        self.client.work = [
            tool_response(tool_call(path="example.py")), final_response()
        ]
        result = self.run_state()
        self.assertEqual(result.status, "completed")
        permission.assert_not_called()
        self.read_file.assert_called_once()

    def test_main_shell_timeout_still_requests_cleanup(self):
        """子 Agent 禁用 shell 后，主 Agent 的原始进程超时清理仍保留。"""
        self.process.communicate.side_effect = subprocess.TimeoutExpired("fake", 3)
        output, code = self.agent._run_bash_process("fake", timeout=3)
        self.assertIsNone(code)
        self.assertIn("timed out", output)
        self.process.communicate.assert_called_once_with(timeout=3)
        self.agent._stop_process_group.assert_called_once_with(self.process)
        self.assertFalse(self.agent._shell_processes)

    def test_subagent_rejects_mutating_tools_and_keeps_evidence(self):
        """即使模型请求了未公布的工具，也不允许执行或启动 shell。"""
        self.client.work = [tool_response(
            tool_call("bash", command="must not run"),
            tool_call("write_file", call_id="call_2", path="x", content="x"),
            tool_call("edit_file", call_id="call_3", path="x",
                      old_content="x", new_content="y"),
        ), final_response()]
        payload = json.loads(self.agent.format_subagent_result(self.run_state()))
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(len(payload["evidence"]), 3)
        for item in payload["evidence"]:
            self.assertTrue(item["output"]["text"].startswith("Error:"))
            self.assertNotIn("exit_code", item)
        self.agent.subprocess.Popen.assert_not_called()

    def test_invalid_summary_format_preserves_text(self):
        """模型没有遵守纯 JSON 格式时，原始回答仍能交接。"""
        text = 'Report\n```json\n{"summary":"done","remaining":""}\n```'
        self.client.work = [types.SimpleNamespace(content=[FakeBlock("text", text=text)])]
        result = self.run_state()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.summary, text)
        self.assertTrue(result.remaining)

    def test_invalid_task_does_not_start_model(self):
        """未认领或不存在的任务，不应启动子 Agent。"""
        self.agent.load_task.return_value = types.SimpleNamespace(status="pending", owner=None)
        payload = json.loads(self.agent.run_subagent("task_12345678", "test"))
        self.assertIsNone(payload["run_id"])
        self.assertTrue(payload["error"])
        self.agent.load_task.side_effect = FileNotFoundError("unknown task")
        payload = json.loads(self.agent.run_subagent("task_87654321", "test"))
        self.assertIsNone(payload["run_id"])
        self.assertEqual(self.client.requests, [])

    def test_all_evidence_retained_and_truncation_marked(self):
        """超过 8 条也保留全部条目，长输出显式标记截断。"""
        self.read_file.return_value = "x" * 1500
        self.client.work = [tool_response(*[
            tool_call(call_id=f"call_{i}", path=f"file_{i}.py") for i in range(10)
        ]), final_response()]
        result = self.run_state()
        payload = json.loads(self.agent.format_subagent_result(result))
        self.assertEqual(len(payload["evidence"]), 10)
        for record in payload["evidence"]:
            self.assertTrue(record["output"]["truncated"])
            self.assertEqual(len(record["output"]["text"]), 1200)
        self.assertNotIn("record_path", payload)


    def test_thinking_only_response_reports_failure_without_retry(self):
        self.client.work = [types.SimpleNamespace(
            content=[FakeBlock("thinking", thinking="not a final answer")],
            stop_reason="end_turn",
            usage=types.SimpleNamespace(output_tokens=42),
        )]
        result = self.run_state()
        self.assertEqual(result.status, "failed")
        self.assertIn("thinking", result.error)
        self.assertEqual(result.turns_used, 1)
        record = result.response_log[0]
        self.assertEqual(record["stop_reason"], "end_turn")
        self.assertEqual(record["content_types"], ["thinking"])
        self.assertEqual(record["output_tokens"], 42)
        self.assertNotIn("not a final answer", json.dumps(record))
        self.assertEqual(len(self.client.requests), 2)  # 工作一次，收尾一次
        self.assertEqual(result.response_log[-1]["phase"], "summary")

    def test_output_exhaustion_never_completes_or_executes_partial_tools(self):
        for content in (
            [FakeBlock("text", text='{"summary":"looks done","remaining":""}')],
            [tool_call(path="must_not_run.py")],
        ):
            with self.subTest(content=content):
                self.client.work = [types.SimpleNamespace(
                    content=content, stop_reason="max_tokens",
                    usage=types.SimpleNamespace(output_tokens=8192),
                )]
                self.client.summary = [final_response("partial", "unfinished")]
                result = self.run_state()
                self.assertEqual(result.status, "budget_exhausted")
                self.assertTrue(result.error)
                self.read_file.assert_not_called()
                self.assertEqual(self.evidence(result), [])

    def test_empty_summary_preserves_program_fallback(self):
        self.client.work = [tool_response(tool_call(path="example.py"))]
        self.client.summary = [types.SimpleNamespace(
            content=[], stop_reason="end_turn",
            usage=types.SimpleNamespace(output_tokens=0),
        )]
        result = self.run_state(max_turns=1)
        self.assertEqual(result.status, "budget_exhausted")
        self.assertIn("budget_exhausted", result.summary)
        self.assertTrue(result.remaining)
        self.assertTrue(result.warnings)
        self.assertEqual(result.response_log[-1]["content_types"], [])
        self.assertEqual(result.response_log[-1]["output_tokens"], 0)

    def test_truncated_summary_does_not_replace_fallback(self):
        self.client.work = [tool_response(tool_call(path="example.py"))]
        response = final_response("not reliable", "")
        response.stop_reason = "max_tokens"
        self.client.summary = [response]
        result = self.run_state(max_turns=1)
        self.assertNotEqual(result.summary, "not reliable")
        self.assertTrue(result.remaining)
        self.assertTrue(result.warnings)

    def test_paged_read_arguments_reach_the_tool_and_evidence(self):
        self.client.work = [
            tool_response(tool_call(path="main.py", offset=1600, limit=120)),
            final_response(),
        ]
        result = self.run_state()
        self.read_file.assert_called_once_with(
            path="main.py", offset=1600, limit=120
        )
        self.assertEqual(self.evidence(result)[0]["arguments"]["offset"], 1600)

    def test_manager_publishes_failure_and_keeps_query_after_collect(self):
        self.client.work = [types.SimpleNamespace(content=[], stop_reason="end_turn")]
        state = self.state()
        manager = self.agent.SUBAGENTS
        manager.running[state.run_id] = threading.current_thread()
        self.assertEqual(manager.get(state.run_id)["status"], "running")
        manager._run(state)
        first = manager.get(state.run_id)
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["response_log"][0]["content_types"], [])
        first["response_log"].clear()
        self.assertTrue(state.response_log)
        self.assertEqual(manager.collect(), [state])
        self.assertEqual(manager.collect(), [])
        self.assertEqual(manager.get(state.run_id)["status"], "failed")


class ReadFilePaginationTests(unittest.TestCase):
    def setUp(self):
        self.agent, _, _ = load_runtime(FakeClock(), FakeClient())
        self.temp = tempfile.TemporaryDirectory(prefix="agent-read-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.agent.WORKDIR = self.root
        self.lines = [f"source line {i}" for i in range(1, 506)]
        (self.root / "large.py").write_text("\n".join(self.lines), encoding="utf-8")

    def test_every_page_has_correct_lines_and_next_offset(self):
        seen = []
        for offset, last in ((1, 200), (201, 400), (401, 505)):
            output = self.agent.real_read_file("large.py", offset=offset)
            self.assertIn(f"Lines {offset}-{last} of 505", output)
            numbered = output.splitlines()[2:-1]
            self.assertEqual(numbered[0], f"{offset}: source line {offset}")
            self.assertEqual(numbered[-1], f"{last}: source line {last}")
            seen.extend(numbered)
            self.assertTrue(output.endswith(
                f"Next offset: {last + 1}" if last < 505 else "[EOF]"
            ))
        self.assertEqual(seen, [
            f"{i}: source line {i}" for i in range(1, 506)
        ])

    def test_targeted_page_and_legacy_positional_limit(self):
        output = self.agent.real_read_file("large.py", 3, offset=350)
        self.assertIn("Lines 350-352 of 505", output)
        self.assertIn("350: source line 350", output)
        self.assertNotIn("1: source line 1\n", output)
        self.assertTrue(output.endswith("Next offset: 353"))

    def test_large_limit_and_none_still_return_bounded_pages(self):
        for limit in (None, 3600):
            with self.subTest(limit=limit):
                output = self.agent.real_read_file("large.py", limit=limit)
                self.assertIn("Lines 1-200 of 505", output)
                self.assertTrue(output.endswith("Next offset: 201"))

    def test_eof_empty_file_and_invalid_arguments(self):
        self.assertIn("[EOF]", self.agent.real_read_file("large.py", offset=506))
        (self.root / "empty.py").write_text("", encoding="utf-8")
        self.assertIn("Total lines: 0", self.agent.real_read_file("empty.py"))
        for args in ({"offset": 0}, {"offset": True}, {"offset": 1.5},
                     {"limit": 0}, {"limit": -1}, {"limit": "3"}):
            with self.subTest(args=args):
                self.assertTrue(self.agent.real_read_file(
                    "large.py", **args
                ).startswith("Error:"))

    def test_workspace_escape_and_missing_file_are_errors(self):
        self.assertIn("escapes", self.agent.real_read_file("../outside.py"))
        self.assertTrue(self.agent.real_read_file("missing.py").startswith("Error:"))

    def test_tool_schema_advertises_pagination_for_both_agents(self):
        main_tool = next(t for t in self.agent.BASE_TOOLS if t["name"] == "read_file")
        sub_tool = next(t for t in self.agent.SUB_TOOLS if t["name"] == "read_file")
        self.assertEqual(main_tool, sub_tool)
        self.assertIn("offset", sub_tool["input_schema"]["properties"])
        self.assertEqual({t["name"] for t in self.agent.SUB_TOOLS},
                         {"read_file", "glob", "load_skill"})



if __name__ == "__main__":
    # Windows 重定向输出时也使用 UTF-8，避免中文用例说明乱码。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    print("Offline tests: real subagent functions, fake model/tools/processes.")
    print("No API calls, no shell commands, no task-board changes.", flush=True)
    unittest.main(verbosity=2)
