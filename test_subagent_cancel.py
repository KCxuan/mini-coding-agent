"""Cancellation regression tests.

Run: python -B test_subagent_cancel.py
Loads selected real main.py definitions through the existing AST harness.
Uses real threads and Events, but fake model requests and file tools.
Does not import main, read credentials, call APIs, or change the task board.
Does not test CLI shutdown or interruption of real SDK/network requests.
"""

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_subagent_stage1 import (
    FakeAPITimeoutError,
    FakeClock,
    FakeClient,
    final_response,
    load_runtime,
    tool_call,
    tool_response,
)


class SubagentCancellationTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.agent, self.read_file, _ = load_runtime(FakeClock(), self.client)

    def state(self):
        return self.agent.SubagentState(task_id="task_A", prompt="A")

    def test_cancel_before_start_skips_model(self):
        state = self.state()
        state.cancel_event.set()
        result = self.agent.execute_subagent(state)
        self.assertIs(result, state)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.client.requests, [])
        self.read_file.assert_not_called()
        self.assertTrue(result.summary)
        self.assertTrue(result.remaining)

    def test_cancel_during_tool_preserves_result_and_skips_next_tool(self):
        state = self.state()
        self.client.work = [tool_response(
            tool_call(path="first.py", call_id="first"),
            tool_call(path="second.py", call_id="second"),
        )]

        def read(**kwargs):
            state.cancel_event.set()
            return "verified first file content"

        self.read_file.side_effect = read
        result = self.agent.execute_subagent(state)
        self.assertEqual(result.status, "cancelled")
        self.read_file.assert_called_once_with(path="first.py")
        evidence = self.agent.subagent_evidence(result)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["output"], "verified first file content")
        self.assertEqual(len(self.client.requests), 1)

    def test_tool_dispatch_propagates_cancel_exception(self):
        def cancelled(**kwargs):
            raise self.agent.SubagentCancelled("cancel")
        with self.assertRaises(self.agent.SubagentCancelled):
            self.agent.execute_subagent_tool(
                tool_call(path="first.py"), {"read_file": cancelled}
            )

    def test_cancel_during_model_failure_preserves_error_without_summary_call(self):
        for error in (RuntimeError("API failed"), FakeAPITimeoutError("API timeout")):
            with self.subTest(error=type(error).__name__):
                client = FakeClient()
                agent, read_file, _ = load_runtime(FakeClock(), client)
                state = agent.SubagentState(task_id="task_A", prompt="A")

                def fail():
                    state.cancel_event.set()
                    raise error

                client.work = [fail]
                result = agent.execute_subagent(state)
                self.assertEqual(result.status, "cancelled")
                self.assertIn(str(error), result.error)
                self.assertEqual(len(client.requests), 1)
                read_file.assert_not_called()

    def test_cancel_during_existing_summary_keeps_returned_summary(self):
        state = self.state()

        def summarize():
            state.cancel_event.set()
            return final_response("existing findings", "check remaining code")

        self.client.summary = [summarize]
        result = self.agent.finalize_subagent(state, "budget_exhausted")
        self.assertEqual(result.status, "cancelled")
        self.assertIn("existing findings", result.summary)
        self.assertIn("check remaining code", result.remaining)
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(result.response_log[0]["phase"], "summary")

    def test_cancel_just_before_summary_request_skips_request(self):
        state = self.state()
        check = self.agent.check_subagent_cancelled

        def cancel_at_check(current):
            current.cancel_event.set()
            check(current)

        with patch.object(self.agent, "check_subagent_cancelled", cancel_at_check):
            result = self.agent.finalize_subagent(state, "budget_exhausted")
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.client.requests, [])

    def test_cancel_before_publication_wins_and_notification_is_injected_once(self):
        manager = self.agent.SUBAGENTS
        state = self.state()
        manager.running[state.run_id] = threading.current_thread()
        manager.cancel_events[state.run_id] = state.cancel_event

        def finish_then_cancel(current):
            current.status = "completed"
            current.summary = "already produced findings"
            current.remaining = ""
            self.assertEqual(manager.cancel(current.run_id)["status"], "cancelling")
            return current

        with patch.object(self.agent, "execute_subagent", finish_then_cancel):
            manager._run(state)

        self.assertEqual(manager.get(state.run_id)["status"], "cancelled")
        self.assertIn("already produced findings", state.summary)
        self.assertNotIn(state.run_id, manager.cancel_events)
        messages = []
        self.assertEqual(self.agent.inject_subagent_results(messages), 1)
        self.assertIn('"status": "cancelled"', messages[-1]["content"][0]["text"])
        self.assertEqual(self.agent.inject_subagent_results(messages), 0)
        self.assertEqual(manager.get(state.run_id)["status"], "cancelled")

    def test_thread_start_failure_releases_slot_and_event(self):
        manager = self.agent.SUBAGENTS
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("start failed")):
            with self.assertRaisesRegex(RuntimeError, "start failed"):
                manager.start("task_A", "A")
        self.assertEqual(manager.running, {})
        self.assertEqual(manager.cancel_events, {})
        self.assertEqual(manager.results, {})
        self.assertEqual(manager.collect(), [])

    def test_cancel_a_preserves_evidence_and_b_completes(self):
        a_waiting = threading.Event()
        release_a = threading.Event()
        calls = {"A": 0, "B": 0}
        calls_lock = threading.Lock()

        def create(**request):
            if "tools" not in request:
                raise AssertionError("Cancellation must not start a summary request")
            label = request["messages"][0]["content"]
            with calls_lock:
                calls[label] += 1
                turn = calls[label]
            if label == "B":
                return final_response("B completed")
            if turn == 1:
                return tool_response(tool_call(path="example.py", call_id="a_first_read"))
            a_waiting.set()
            if not release_a.wait(timeout=10):
                raise RuntimeError("Test did not release A")
            return tool_response(
                tool_call(path="must_not_read.py", call_id="a_forbidden_read")
            )

        client = SimpleNamespace(with_options=lambda **options: SimpleNamespace(
            messages=SimpleNamespace(create=create)
        ))
        agent, read_file, _ = load_runtime(FakeClock(), client)
        manager = agent.SUBAGENTS
        threads = []
        try:
            run_a = manager.start("task_A", "A")
            with manager._lock:
                thread_a = manager.running.get(run_a)
                if thread_a is not None:
                    threads.append(thread_a)
            self.assertTrue(a_waiting.wait(timeout=3))
            self.assertIsNotNone(thread_a)

            self.assertEqual(manager.cancel(run_a)["status"], "cancelling")
            self.assertEqual(manager.get(run_a)["status"], "cancelling")
            self.assertEqual(manager.cancel(run_a)["status"], "cancelling")

            # A keeps its slot while its admitted request has not returned.
            manager.max_workers = 1
            with self.assertRaises(RuntimeError):
                manager.start("task_B", "B")
            manager.max_workers = 2
            run_b = manager.start("task_B", "B")
            with manager._lock:
                thread_b = manager.running.get(run_b)
                if thread_b is not None:
                    threads.append(thread_b)
            if thread_b is not None:
                thread_b.join(timeout=3)
                self.assertFalse(thread_b.is_alive())
            self.assertEqual(manager.get(run_b)["status"], "completed")
            self.assertEqual(
                [state.run_id for state in manager.collect()], [run_b]
            )
            self.assertTrue(manager.has_running())

            release_a.set()
            thread_a.join(timeout=3)
            self.assertFalse(thread_a.is_alive())
            result = manager.get(run_a)
            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(len(result["evidence"]), 1)
            self.assertEqual(result["evidence"][0]["tool_use_id"], "a_first_read")
            read_file.assert_called_once_with(path="example.py")
            self.assertEqual(calls, {"A": 2, "B": 1})
            self.assertEqual(
                [state.run_id for state in manager.collect()], [run_a]
            )
            self.assertEqual(manager.collect(), [])
            self.assertFalse(manager.has_running())
            self.assertEqual(manager.cancel_events, {})
            self.assertEqual(manager.cancel(run_b)["status"], "completed")
            self.assertEqual(manager.cancel("unknown_run")["status"], "not_found")

            # Querying returns a copy, not the published mutable object.
            result["evidence"].clear()
            self.assertEqual(len(manager.get(run_a)["evidence"]), 1)
        finally:
            release_a.set()
            for thread in threads:
                thread.join(timeout=3)

    def test_cancel_tool_wrapper_and_readonly_boundary(self):
        receipt = json.loads(self.agent.run_subagent_cancel("unknown_run"))
        self.assertEqual(receipt["status"], "not_found")
        self.assertEqual(self.agent.SUBAGENT_CANCEL_TOOL["name"], "subagent_cancel")
        self.assertNotIn("subagent_cancel", self.agent.SUB_READONLY_TOOL_NAMES)
        self.assertNotIn("subagent_cancel", [tool["name"] for tool in self.agent.SUB_TOOLS])


if __name__ == "__main__":
    unittest.main(verbosity=2)
