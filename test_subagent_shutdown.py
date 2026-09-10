"""Offline shutdown tests for real definitions in main.py.

Run: python -B test_subagent_shutdown.py
No main import, credentials, API calls or business-file edits.
The startup-deadlock probe runs in an isolated process with a hard timeout.
CLI tests execute the actual __main__ block with fake input/model/resources.
Real OS signal delivery and live SDK/network cleanup are not tested.
"""
from __future__ import annotations

import ast
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_subagent_stage1 import (
    SOURCE, FakeClient, FakeClock, load_runtime, tool_call, tool_response,
)


def runtime(clock=None):
    agent, read, _ = load_runtime(clock or time, FakeClient())
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    names = {"cleanup_program", "_handle_termination_signal"}
    definitions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert len(definitions) == 2
    exec(compile(ast.Module(body=definitions, type_ignores=[]),
                 str(SOURCE), "exec"), agent.__dict__)
    agent._cleanup_started = False
    agent._exit_requested = False
    agent._stop_all_shell_processes = Mock()
    agent.disconnect_all_mcp = Mock()
    return agent, read


def start_failure_probe():
    agent, _ = runtime()
    manager = agent.SUBAGENTS
    with patch.object(threading.Thread, "start",
                      side_effect=RuntimeError("simulated thread start failure")):
        try:
            manager.start("task_A", "A")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected thread start failure")
    assert not manager.running
    assert not manager.cancel_events
    print("START_FAILURE_HANDLED", flush=True)


class ShutdownTests(unittest.TestCase):
    def test_start_failure_returns_without_deadlock(self):
        try:
            result = subprocess.run(
                [sys.executable, "-B", str(Path(__file__).resolve()), "--start-failure-probe"],
                capture_output=True, text=True, timeout=3,
            )
        except subprocess.TimeoutExpired:
            self.fail(
                "start() did not return within 3 seconds after Thread.start raised. "
                "The exception branch reacquires the non-reentrant lock already held "
                "by start(); isolated probe was killed and reaped."
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("START_FAILURE_HANDLED", result.stdout)

    def test_shutdown_empty_is_repeatable_and_rejects_new_runs(self):
        agent, _ = runtime()
        manager = agent.SUBAGENTS
        self.assertEqual(manager.shutdown(0), [])
        self.assertEqual(manager.shutdown(0), [])
        with self.assertRaises(RuntimeError):
            manager.start("task_A", "A")
        self.assertEqual(manager.running, {})
        self.assertEqual(manager.cancel_events, {})

    def test_negative_budget_rejected_without_closing(self):
        agent, _ = runtime()
        with self.assertRaises(ValueError):
            agent.SUBAGENTS.shutdown(-1)
        self.assertFalse(agent.SUBAGENTS._closing)

    def test_wait_uses_shared_deadline_and_releases_lock(self):
        clock = FakeClock()
        agent, _ = runtime(clock)
        manager = agent.SUBAGENTS
        waits = []
        events = {name: threading.Event() for name in ("A", "B", "C")}
        manager.cancel_events.update(events)

        def join_for(name):
            def join(timeout):
                self.assertTrue(all(event.is_set() for event in events.values()))
                self.assertTrue(manager._lock.acquire(blocking=False))
                manager._lock.release()
                waits.append((name, timeout))
                clock.advance(min(3, timeout))
            return join

        manager.running.update({
            name: SimpleNamespace(join=join_for(name)) for name in events
        })
        self.assertEqual(manager.shutdown(5), ["A", "B", "C"])
        self.assertEqual(waits, [("A", 5.0), ("B", 2.0)])
        self.assertEqual(clock.now, 5)
        self.assertEqual(manager.results, {})

    def test_shutdown_cancels_two_real_workers_and_publishes_results(self):
        agent, read = runtime()
        manager = agent.SUBAGENTS
        entered = {"A": threading.Event(), "B": threading.Event()}
        workers = []

        def create(**request):
            label = request["messages"][0]["content"]
            run_id = threading.current_thread().name
            with manager._lock:
                cancel_event = manager.cancel_events[run_id]
            entered[label].set()
            if not cancel_event.wait(timeout=3):
                raise RuntimeError("Shutdown failed to notify worker")
            return tool_response(tool_call(path="must_not_read.py"))

        agent.client = SimpleNamespace(with_options=lambda **options: SimpleNamespace(
            messages=SimpleNamespace(create=create)
        ))
        try:
            ids = [manager.start("task_" + label, label) for label in entered]
            with manager._lock:
                workers = list(manager.running.values())
            self.assertTrue(all(event.wait(2) for event in entered.values()))
            self.assertEqual(manager.shutdown(2), [])
            self.assertEqual({manager.get(run_id)["status"] for run_id in ids}, {"cancelled"})
            self.assertEqual({state.run_id for state in manager.collect()}, set(ids))
            self.assertEqual(manager.collect(), [])
            read.assert_not_called()
            self.assertEqual(manager.cancel_events, {})
        finally:
            with manager._lock:
                for event in manager.cancel_events.values():
                    event.set()
            for worker in workers:
                worker.join(timeout=3)

    def test_wait_timeout_does_not_fake_cancelled_state(self):
        agent, _ = runtime()
        manager = agent.SUBAGENTS
        entered = threading.Event()
        release = threading.Event()
        worker = None

        def create(**request):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Test failed to release pending request")
            return tool_response(tool_call(path="must_not_read.py"))

        agent.client = SimpleNamespace(with_options=lambda **options: SimpleNamespace(
            messages=SimpleNamespace(create=create)
        ))
        try:
            run_id = manager.start("task_A", "A")
            with manager._lock:
                worker = manager.running[run_id]
            self.assertTrue(entered.wait(2))
            started = time.monotonic()
            self.assertEqual(manager.shutdown(0.05), [run_id])
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(manager.get(run_id)["status"], "cancelling")
            self.assertEqual(manager.collect(), [])
            self.assertTrue(worker.is_alive())
            release.set()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(manager.get(run_id)["status"], "cancelled")
        finally:
            release.set()
            if worker is not None:
                worker.join(timeout=3)

    def test_cleanup_runs_once_even_if_called_again_by_atexit(self):
        agent, _ = runtime()
        with patch.object(agent.SUBAGENTS, "shutdown", wraps=agent.SUBAGENTS.shutdown) as close:
            agent.cleanup_program()
            agent.cleanup_program()
        close.assert_called_once_with(timeout_seconds=5.0)
        agent._stop_all_shell_processes.assert_called_once()
        agent.disconnect_all_mcp.assert_called_once()

    def test_cleanup_failure_does_not_skip_other_resources(self):
        agent, _ = runtime()
        agent.SUBAGENTS.shutdown = Mock(side_effect=RuntimeError("shutdown failure"))
        agent._stop_all_shell_processes.side_effect = RuntimeError("shell failure")
        agent.cleanup_program()
        agent.disconnect_all_mcp.assert_called_once()
        output = " ".join(str(call.args) for call in agent.print.call_args_list)
        self.assertIn("shutdown failure", output)
        self.assertIn("shell failure", output)

    def test_signal_requests_exit_once_and_cleanup_is_separate(self):
        agent, _ = runtime()
        with self.assertRaises(SystemExit) as raised:
            agent._handle_termination_signal(signal.SIGINT, None)
        self.assertEqual(raised.exception.code, 128 + signal.SIGINT)
        self.assertTrue(agent._exit_requested)
        self.assertFalse(agent._cleanup_started)
        agent._stop_all_shell_processes.assert_not_called()
        agent._handle_termination_signal(signal.SIGINT, None)
        agent.cleanup_program()
        agent._handle_termination_signal(signal.SIGINT, None)
        agent._stop_all_shell_processes.assert_called_once()

    def test_signal_during_normal_exit_cleanup_is_ignored(self):
        agent, _ = runtime()
        agent._stop_all_shell_processes.side_effect = (
            lambda: agent._handle_termination_signal(signal.SIGINT, None)
        )
        agent.cleanup_program()
        self.assertFalse(agent._exit_requested)
        agent.disconnect_all_mcp.assert_called_once()

    def test_actual_cli_block_always_runs_cleanup(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
        entry = next(node for node in reversed(tree.body) if isinstance(node, ast.If))
        code = compile(ast.Module(body=entry.body, type_ignores=[]), str(SOURCE), "exec")
        for scenario in ("exit", "eof", "error", "signal"):
            with self.subTest(scenario=scenario):
                agent, _ = runtime()
                env = agent.__dict__
                env["os"] = SimpleNamespace(getcwd=lambda: "offline-workspace")
                env["trigger_hooks"] = Mock()
                env["input"] = Mock(
                    side_effect=EOFError() if scenario == "eof" else None,
                    return_value="exit" if scenario == "exit" else "test request",
                )
                if scenario == "error":
                    env["agent_loop"] = Mock(side_effect=RuntimeError("model failure"))
                    with self.assertRaisesRegex(RuntimeError, "model failure"):
                        exec(code, env)
                elif scenario == "signal":
                    env["agent_loop"] = lambda *args: agent._handle_termination_signal(
                        signal.SIGINT, None
                    )
                    with self.assertRaises(SystemExit):
                        exec(code, env)
                else:
                    env["agent_loop"] = Mock()
                    exec(code, env)
                    env["agent_loop"].assert_not_called()
                agent._stop_all_shell_processes.assert_called_once()
                agent.disconnect_all_mcp.assert_called_once()

    def test_atexit_registration_leaves_only_unified_cleanup(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
        callbacks = []
        functions = {
            name: Mock(name=name) for name in
            ("cleanup_program", "_stop_all_shell_processes", "disconnect_all_mcp")
        }
        env = dict(functions, atexit=SimpleNamespace(
            register=lambda callback: callbacks.append(callback),
            unregister=lambda callback: callbacks.remove(callback),
        ))
        for node in tree.body:
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            if (isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "atexit"):
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), env)
        self.assertEqual(callbacks, [functions["cleanup_program"]])


if __name__ == "__main__":
    if "--start-failure-probe" in sys.argv:
        start_failure_probe()
    else:
        unittest.main(verbosity=2)
