"""外部验收：仅由实验启动器在 Agent 返回后执行。"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def expected(info=0, warning=0, error=0, invalid=0):
    return {"total_valid": info + warning + error, "invalid_lines": invalid,
            "counts": {"INFO": info, "WARNING": warning, "ERROR": error}}


def run_checks(workspace):
    program = workspace / "log_report.py"
    if not program.is_file():
        return [{"name": "entrypoint_exists", "passed": False, "detail": "Missing log_report.py"}]
    scenarios = [
        ("normal_levels", '\n'.join(json.dumps({"level": level, "message": "ok"})
                                   for level in ("INFO", "INFO", "WARNING", "ERROR")), expected(2, 1, 1)),
        ("blank_and_malformed", '\n  \nnot-json\n{"level":"INFO","message":"ok"}\n', expected(1, invalid=1)),
        ("empty_input", '', expected()),
        ("schema_validation", '\n'.join([
            '[]', 'null', '123', '{}', '{"level":"DEBUG","message":"x"}',
            '{"level":"info","message":"x"}', '{"level":"INFO","message":3}',
            '{"level":"ERROR","message":"","extra":true}',
        ]), expected(error=1, invalid=7)),
        ("unicode_and_nested_output", '{"level":"WARNING","message":"中文,引号\\\"内容"}\n', expected(warning=1)),
        ("continue_after_multiple_bad_lines", '{bad}\n{"level":"ERROR","message":"a"}\n{bad}\n{"level":"INFO","message":"b"}', expected(1, error=1, invalid=2)),
    ]
    results = []
    # 数据和验收结果不放到 Agent 的工作区，避免开发自测覆盖验收输入。
    with tempfile.TemporaryDirectory(prefix="agent-experiment-grade-") as temporary:
        root = Path(temporary)
        for number, (name, content, wanted) in enumerate(scenarios):
            folder = root / str(number)
            folder.mkdir()
            source = folder / "input with spaces.jsonl"
            output = folder / "nested" / "report.json"
            source.write_text(content, encoding="utf-8")
            before = source.read_bytes()
            try:
                process = subprocess.run(
                    [sys.executable, "-X", "utf8", "-B", str(program),
                     "--input", str(source), "--output", str(output)],
                    cwd=workspace, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=10,
                )
                actual = json.loads(output.read_text(encoding="utf-8")) if output.is_file() else None
                valid_types = (
                    isinstance(actual, dict)
                    and type(actual.get("total_valid")) is int
                    and type(actual.get("invalid_lines")) is int
                    and isinstance(actual.get("counts"), dict)
                    and all(type(value) is int for value in actual["counts"].values())
                )
                passed = (process.returncode == 0 and actual == wanted
                          and valid_types and source.read_bytes() == before)
                results.append({"name": name, "passed": passed, "exit_code": process.returncode,
                                "expected": wanted, "actual": actual,
                                "stderr": process.stderr[-1500:]})
            except Exception as error:
                results.append({"name": name, "passed": False, "detail": f"{type(error).__name__}: {error}"})
        for name, args, want_zero in (
            ("help", ["--help"], True),
            ("missing_input", ["--input", str(root / "does-not-exist.jsonl"),
                               "--output", str(root / "missing-report.json")], False),
        ):
            try:
                process = subprocess.run([sys.executable, "-X", "utf8", "-B", str(program), *args],
                                         cwd=workspace, capture_output=True, text=True,
                                         encoding="utf-8", errors="replace", timeout=10)
                passed = ((process.returncode == 0 and bool((process.stdout + process.stderr).strip()))
                          if want_zero else process.returncode != 0)
                results.append({"name": name, "passed": passed, "exit_code": process.returncode,
                                "stderr": process.stderr[-1500:]})
            except Exception as error:
                results.append({"name": name, "passed": False, "detail": f"{type(error).__name__}: {error}"})
    return results
