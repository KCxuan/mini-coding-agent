"""测试替身与隔离设施；这里不复制被测业务逻辑。"""

import ast
import copy
import json
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class IsolatedTestCase(unittest.TestCase):
    """每个用例一个临时工作区，并阻止误联网或启动外部进程。"""

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(prefix="coding-agent-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()
        for target in ("socket.socket.connect", "socket.socket.connect_ex",
                       "socket.create_connection", "subprocess.Popen"):
            self.enterContext(patch(target, side_effect=AssertionError(
                "Offline test attempted network access or process creation")))


class Block(SimpleNamespace):
    """只实现业务代码使用的 SDK 响应块接口。"""

    def model_dump(self, **kwargs):
        return copy.deepcopy(vars(self))


def text_response(text=None, *, stop_reason="end_turn"):
    if text is None:
        text = json.dumps({"summary": "Read the requested file", "remaining": ""})
    return SimpleNamespace(
        content=[Block(type="text", text=text)], stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def tool_call(name="read_file", arguments=None, call_id="call_1"):
    return Block(type="tool_use", id=call_id, name=name,
                 input=arguments if arguments is not None else {"path": "sample.txt"})


def tool_response(*calls, stop_reason="tool_use"):
    return SimpleNamespace(
        content=list(calls), stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


class ScriptedClient:
    """按顺序返回响应/抛出异常；额外请求会报错，防止悄悄通过。"""

    def __init__(self, *responses):
        self.responses = deque(responses)
        self.calls = []
        self.options = []
        self.messages = self

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self

    def create(self, **kwargs):
        # 固定请求时刻的快照，后续 messages 修改不能污染证据。
        self.calls.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("Unexpected additional model request")
        response = self.responses.popleft()
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            response = response()
        return response


def load_main_functions(namespace, *names):
    """从当前 main.py 提取原函数 AST，避开顶层客户端/信号/atexit 初始化。

    这是主循环逻辑的隔离测试，不是 CLI 启动测试。函数体来自磁盘，
    没有维护另一份 agent_loop；依赖由每个测试显式传入。
    """
    path = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    found = {node.name for node in nodes}
    if found != set(names):
        raise AssertionError(f"main.py missing functions: {set(names) - found}")
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)
    return namespace
