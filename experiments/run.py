"""手动运行入口：只在用户执行此脚本时调用真实模型。"""

import argparse
import builtins
import hashlib
import json
import os
import runpy
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from grade_build import run_checks
from tasks import BUILD_TASK, REVIEW_FILES, SAMPLE, review_prompt


class Tee:
    def __init__(self, terminal, logfile, lock):
        self.terminal, self.logfile, self.lock = terminal, logfile, lock

    def write(self, value):
        with self.lock:
            self.terminal.write(value)
            self.terminal.flush()
            if not self.logfile.closed:
                self.logfile.write(value)
                self.logfile.flush()
        return len(value)

    def flush(self):
        self.terminal.flush()
        if not self.logfile.closed:
            self.logfile.flush()

    def __getattr__(self, name):
        return getattr(self.terminal, name)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    parser = argparse.ArgumentParser(description="Run one real-model experiment and save its evidence")
    parser.add_argument("mode", choices=("build", "serial", "parallel"))
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    repo = package.parent
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + args.mode + "-" + uuid4().hex[:8]
    directory = package / "runs" / run_id
    workspace = directory / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    if (repo / "skills").is_dir():
        shutil.copytree(repo / "skills", workspace / "skills")
    prompt = BUILD_TASK if args.mode == "build" else review_prompt(args.mode)
    (workspace / "TASK.md").write_text(prompt + "\n", encoding="utf-8")
    if args.mode == "build":
        (workspace / "sample.jsonl").write_text(SAMPLE, encoding="utf-8")
    else:
        for name, source in REVIEW_FILES.items():
            (workspace / name).write_text(source, encoding="utf-8")
    initial = {str(path.relative_to(workspace)): file_hash(path)
               for path in workspace.rglob("*") if path.is_file()}
    production = [repo / "main.py", *sorted((repo / "CodingAgent").rglob("*.py"))]
    code_hash = hashlib.sha256(b"".join(
        str(path.relative_to(repo)).encode() + b"\0" + path.read_bytes() for path in production)).hexdigest()
    result = {
        "experiment": args.mode, "experiment_run_id": run_id, "agent_source_sha256": code_hash,
        "python": sys.version.split()[0], "status": "initializing", "interactive_prompts": 0,
        "elapsed_seconds": None, "subagent_intervals": [], "peak_active_subagents": 0,
        "review_tool_violations": [], "result_directory": str(directory),
    }
    state_lock = threading.Lock()
    log_lock = threading.Lock()
    active = set()
    start_time = None
    ns = None
    history = []
    old_cwd, old_stdout, old_stderr = Path.cwd(), sys.stdout, sys.stderr
    old_input = builtins.input
    sys.path.insert(0, str(repo))

    def observed_input(message=""):
        result["interactive_prompts"] += 1
        return old_input(message)

    print(f"将使用真实模型执行 {args.mode}，会产生模型费用。结果目录：{directory}")
    print("异常长时间未结束时可按 Ctrl+C；本次记录仍会保留。")
    with (directory / "console.txt").open("w", encoding="utf-8") as logfile:
        sys.stdout, sys.stderr = Tee(old_stdout, logfile, log_lock), Tee(old_stderr, logfile, log_lock)
        builtins.input = observed_input
        try:
            os.chdir(workspace)
            # 执行真实入口的初始化；特殊 run_name 避免启动交互 while 循环。
            ns = runpy.run_path(str(repo / "main.py"), run_name="__experiment_agent__")
            result["model"] = ns["MODEL"]
            manager = ns["SUBAGENTS"]
            original_run = manager.executor.execute_subagent

            def observed_run(state):
                with state_lock:
                    active.add(state.run_id)
                    row = {"run_id": state.run_id, "task_id": state.task_id,
                           "prompt": state.prompt, "start_seconds": time.perf_counter() - start_time,
                           "end_seconds": None}
                    result["subagent_intervals"].append(row)
                    result["peak_active_subagents"] = max(result["peak_active_subagents"], len(active))
                try:
                    return original_run(state)
                finally:
                    with state_lock:
                        row["end_seconds"] = time.perf_counter() - start_time
                        row["status"] = state.status
                        row["turns_used"] = state.turns_used
                        row["summary"] = state.summary
                        row["remaining"] = state.remaining
                        row["error"] = state.error
                        active.discard(state.run_id)

            manager.executor.execute_subagent = observed_run

            def observe_tool(block):
                if args.mode != "build" and (block.name in ("bash", "write_file", "edit_file", "connect_mcp")
                                              or block.name.startswith("mcp__")):
                    result["review_tool_violations"].append(block.name)
                return None  # 只记录，不替 Agent 拦截或改变业务行为。

            ns["HOOKS"].register("PreToolUse", observe_tool)
            start_time = time.perf_counter()
            ns["HOOKS"].trigger("UserPromptSubmit", prompt)
            history = [{"role": "user", "content": prompt}]
            ns["agent_loop"](history, prompt)
            result["elapsed_seconds"] = round(time.perf_counter() - start_time, 3)
            result["status"] = "agent_returned"
        except (KeyboardInterrupt, SystemExit) as error:
            result["status"] = "interrupted"
            result["error"] = f"{type(error).__name__}: {error}"
        except Exception as error:
            result["status"] = "error"
            result["error"] = f"{type(error).__name__}: {error}"
            traceback.print_exc()
        finally:
            if result["elapsed_seconds"] is None and start_time is not None:
                result["elapsed_seconds"] = round(time.perf_counter() - start_time, 3)
            if ns is not None:
                try:
                    ns["cleanup_program"]()
                except BaseException as error:
                    result["cleanup_error"] = f"{type(error).__name__}: {error}"
            os.chdir(old_cwd)
            builtins.input = old_input
            sys.stdout, sys.stderr = old_stdout, old_stderr

    if ns is not None:
        manager = ns["SUBAGENTS"]
        with state_lock:
            for row in result["subagent_intervals"]:
                published = manager.get(row["run_id"])
                row["status"] = published["status"]

    final = ""
    for message in reversed(history):
        if message.get("role") == "assistant":
            content = message.get("content", [])
            final = ("\n".join(getattr(block, "text", "") for block in content
                               if getattr(block, "type", None) == "text")
                     if isinstance(content, list) else str(content))
            break
    result["final_answer"] = final
    (directory / "final_answer.txt").write_text(final, encoding="utf-8")
    result["changed_initial_files"] = [name for name, digest in initial.items()
                                         if file_hash(workspace / name) != digest]
    if args.mode == "build" and result["status"] == "agent_returned":
        try:
            checks = run_checks(workspace)
            result["acceptance"] = checks
            result["acceptance_passed"] = sum(check["passed"] for check in checks)
            result["acceptance_total"] = len(checks)
            result["task_passed"] = bool(checks) and all(check["passed"] for check in checks) and not result["changed_initial_files"]
        except BaseException as error:
            result["grading_error"] = f"{type(error).__name__}: {error}"
            result["task_passed"] = False
    elif args.mode != "build":
        runtime_dirs = {"tasks", ".memory", ".transcripts", ".task_outputs", "__pycache__"}
        result["unexpected_new_files"] = [
            str(path.relative_to(workspace)) for path in workspace.rglob("*")
            if path.is_file() and str(path.relative_to(workspace)) not in initial
            and path.relative_to(workspace).parts[0] not in runtime_dirs
        ]
        rows = result["subagent_intervals"]
        result["four_runs_completed"] = len(rows) == 4 and all(row.get("status") == "completed" for row in rows)
        result["scheduling_observation"] = (
            "serial_observed" if args.mode == "serial" and len(rows) == 4 and result["peak_active_subagents"] == 1
            else "parallel_overlap_observed" if args.mode == "parallel" and len(rows) == 4 and result["peak_active_subagents"] >= 2
            else "requested_schedule_not_confirmed"
        )
        result["review_score"] = "pending_human_review"
    # 工作线程通常已结束；如中断后仍在收尾，保存锁内一致快照。
    with state_lock:
        snapshot = json.loads(json.dumps(result, ensure_ascii=False))
    (directory / "result.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    concise = {key: value for key, value in snapshot.items()
               if key not in ("final_answer", "subagent_intervals", "acceptance")}
    concise["subagent_runs"] = [{key: row.get(key) for key in (
        "run_id", "task_id", "start_seconds", "end_seconds", "status")}
        for row in snapshot["subagent_intervals"]]
    if "acceptance" in snapshot:
        concise["checks"] = [{"name": row["name"], "passed": row["passed"]} for row in snapshot["acceptance"]]
    print("\n=== 实验结果 ===")
    print(json.dumps(concise, ensure_ascii=False, indent=2))
    print("\n=== Agent 最终回答（审查内容需要人工核实） ===")
    print(final or "(没有最终文本)")
    print(f"\n请把以上结果贴回对话。完整记录：{directory / 'result.json'}")
    if snapshot["status"] != "agent_returned" or snapshot.get("task_passed") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
