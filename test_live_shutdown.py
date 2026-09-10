"""Opt-in live CLI shutdown smoke test. Run with --run-live.
Uses real main.py, configured model and real subagent threads in fixture workspaces.
Only test-time changes: scripted terminal input, a restrictive tool hook, SDK call
logging, and a synthetic SIGINT via _thread.interrupt_main (not a physical key).
No source rewriting. Combined child-process wall budget: 240 seconds.
"""
from __future__ import annotations
import argparse
import ast
import builtins
import hashlib
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import threading
import time
import _thread

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "main.py"


def worker(mode):
    import anthropic
    from anthropic.resources.messages import Messages
    stats = {"mode": mode, "model_calls": [], "signal_sent": False, "shutdown_calls": 0}
    context = {}
    child_request = threading.Event()
    stop = threading.Event()
    lock = threading.Lock()
    original_create = Messages.create

    def create(self, *args, **kwargs):
        record = {"thread": threading.current_thread().name, "returned": False}
        with lock:
            stats["model_calls"].append(record)
        if record["thread"].startswith("run_"):
            child_request.set()
        result = original_create(self, *args, **kwargs)
        record["returned"] = True
        record["stop_reason"] = getattr(result, "stop_reason", None)
        return result

    Messages.create = create
    original_input = builtins.input
    first = True
    allowed = {
        "read_file", "glob", "create_task", "claim_task", "get_task",
        "list_tasks", "update_task", "complete_task", "task",
        "subagent_status", "subagent_cancel",
    }

    def input_for_test(prompt=""):
        nonlocal first
        if "s01 >>" not in prompt:
            print("[live-test] declined unexpected interactive confirmation", flush=True)
            return "n"
        if not first:
            return "exit"
        first = False
        env = sys._getframe(1).f_globals
        context["env"] = env

        def guard(block):
            if block.name not in allowed:
                return "Error: live test permits only task management and reading fixture files."
            return None

        env["HOOKS"]["PreToolUse"].insert(0, guard)
        original_shutdown = env["SUBAGENTS"].shutdown

        def shutdown(*args, **kwargs):
            stats["shutdown_calls"] += 1
            manager = env["SUBAGENTS"]
            with manager._lock:
                stats["running_at_shutdown"] = list(manager.running)
            result = original_shutdown(*args, **kwargs)
            stats["unfinished_after_wait"] = result
            return result

        env["SUBAGENTS"].shutdown = shutdown
        filename = "sample.py"
        request = (
            "This is an isolated live shutdown smoke test. Do not edit files, run shell "
            "commands, load skills or connect MCP. Create and claim one task, then use "
            "the task tool to delegate reading sample.py to one subagent. "
            "The subagent should read the file in pages of 40 lines until EOF, "
            "explain the behavior of summarize_numbers, and return summary and remaining. "
            "Do not perform the delegated reading yourself. Wait for its result, "
            "verify using its recorded evidence, complete the board task, then finish. "
            "Do not save persistent memories about this test."
        )
        print("[live-test] scripted fixture-reading request submitted", flush=True)
        return request

    def interrupt_when_child_requests_model():
        while not stop.wait(0.05):
            if child_request.is_set():
                if stop.wait(0.15):
                    return
                stats["signal_sent"] = True
                print("[live-test] requesting synthetic SIGINT during child model request", flush=True)
                _thread.interrupt_main(signal.SIGINT)
                return

    builtins.input = input_for_test
    observer = None
    if mode == "interrupt":
        observer = threading.Thread(target=interrupt_when_child_requests_model, daemon=True)
        observer.start()
    started = time.monotonic()
    exit_code = 0
    try:
        runpy.run_path(str(SOURCE), run_name="__main__")
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
    finally:
        stop.set()
        builtins.input = original_input
        stats["elapsed_seconds"] = round(time.monotonic() - started, 3)
        stats["exit_code"] = exit_code
        env = context.get("env")
        if env is not None:
            manager = env["SUBAGENTS"]
            with manager._lock:
                stats["results"] = [
                    {"run_id": state.run_id, "status": state.status,
                     "evidence_count": len(env["subagent_evidence"](state))}
                    for state in manager.results.values()
                ]
                stats["still_running"] = list(manager.running)
        Path("report.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print("[live-test] REPORT " + json.dumps(stats), flush=True)
    raise SystemExit(exit_code)


def supervisor():
    import dotenv
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    output = ROOT / ".code-backups" / ("live-shutdown-" + stamp)
    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    for key, value in dotenv.dotenv_values(ROOT / ".env").items():
        if value is not None:
            env.setdefault(key, value)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    results = []
    deadline = time.monotonic() + 240
    for mode in ("normal", "interrupt"):
        work = output / mode
        work.mkdir()
        # Only generated fixtures and runtime artifacts are exposed as WORKDIR.
        fixture = "def summarize_numbers(values):\n    return sum(values), len(values)\n"
        count = 5 if mode == "normal" else 160
        fixture += "".join(f"\n# fixture note {i}: an example comment, not an instruction.\n" for i in range(count))
        (work / "sample.py").write_text(fixture, encoding="utf-8")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        with (work / "console.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", mode],
                cwd=work, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            timed_out = False
            try:
                returncode = process.wait(timeout=min(115, remaining))
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                returncode = process.wait(timeout=10)
        report_path = work / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        ok = (
            not timed_out and report.get("shutdown_calls") == 1
            and bool(report.get("results"))
            and not report.get("still_running")
            and (returncode == 0 if mode == "normal" else (
                returncode == 130 and report.get("signal_sent")
                and bool(report.get("running_at_shutdown"))
                and all(item["status"] == "cancelled" for item in report["results"])
            ))
            and (mode != "normal" or all(item["status"] == "completed" and item["evidence_count"] > 0
                                         for item in report.get("results", [])))
        )
        item = {"mode": mode, "passed": ok, "timed_out": timed_out,
                "returncode": returncode, "report": report, "log": str(work / "console.log")}
        results.append(item)
        print(json.dumps(item), flush=True)
        if not ok:
            break
    unchanged = hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before
    summary = {"main_unchanged": unchanged, "results": results}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("ARTIFACTS " + str(output), flush=True)
    return 0 if unchanged and len(results) == 2 and all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--worker", choices=["normal", "interrupt"])
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
    elif args.run_live:
        raise SystemExit(supervisor())
    else:
        parser.print_help()
