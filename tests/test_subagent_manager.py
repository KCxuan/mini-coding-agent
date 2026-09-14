"""真实线程+真实管理器；事件控制完成时机，不调用模型。"""

import threading
from unittest.mock import patch

from CodingAgent.subagent.manager import SubagentManager
from CodingAgent.taskboard import TaskBoard, TaskStore
from tests.helpers import IsolatedTestCase


class GatedExecutor:
    def __init__(self):
        self.gates = {}
        self.entered = {}
        self.errors = {}

    def prepare(self, task_id):
        self.gates[task_id] = threading.Event()
        self.entered[task_id] = threading.Event()

    def execute_subagent(self, state):
        self.entered[state.task_id].set()
        if not self.gates[state.task_id].wait(timeout=10):
            raise TimeoutError("Test did not release worker")
        if state.task_id in self.errors:
            raise self.errors[state.task_id]
        state.status = "completed"
        state.summary = "Controlled worker finished"
        state.remaining = ""
        return state


class SubagentManagerTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.board = TaskBoard(TaskStore(self.workdir / "tasks", workdir=self.workdir))
        self.executor = GatedExecutor()
        self.manager = SubagentManager(self.board, self.executor, workdir=self.workdir, max_workers=4)
        self.addCleanup(self.stop_workers)

    def stop_workers(self):
        for gate in self.executor.gates.values():
            gate.set()
        unfinished = self.manager.shutdown(timeout_seconds=3)
        self.assertEqual(unfinished, [], "Test threads did not finish during cleanup")

    def new_task(self, *, claim=True):
        task = self.board.create_task("Controlled review")
        if claim:
            self.board.claim_task(task.id)
        self.executor.prepare(task.id)
        return task.id

    def launch(self, task_id):
        run_id = self.manager.start(task_id, "Read the source")
        self.assertTrue(self.executor.entered[task_id].wait(timeout=3))
        return run_id

    def finish(self, task_id, run_id):
        # Gate 尚未释放，所以运行线程必须仍在登记表里。
        with self.manager._lock:
            thread = self.manager.running[run_id]
        self.executor.gates[task_id].set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive(), "Worker did not publish its final result")
        return self.manager.get(run_id)

    def test_fifth_run_rejected_until_one_slot_is_released(self):
        tasks = [self.new_task() for _ in range(5)]
        runs = [self.launch(task) for task in tasks[:4]]
        with self.assertRaisesRegex(RuntimeError, "并发名额已满"):
            self.manager.start(tasks[4], "Read the source")
        self.assertEqual(len(self.manager.running), 4)
        self.assertEqual(self.finish(tasks[0], runs[0])["status"], "completed")
        fifth = self.launch(tasks[4])
        self.assertEqual(self.manager.get(fifth)["status"], "running")

    def test_simultaneous_launches_cannot_overbook_capacity(self):
        tasks = [self.new_task() for _ in range(8)]
        barrier = threading.Barrier(8)
        outcomes = []
        lock = threading.Lock()

        def contender(task_id):
            try:
                barrier.wait(timeout=3)
                outcome = ("started", self.manager.start(task_id, "Read source"))
            except Exception as exc:
                outcome = ("error", exc)
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=contender, args=(task,), daemon=True) for task in tasks]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sum(kind == "started" for kind, _ in outcomes), 4)
        errors = [value for kind, value in outcomes if kind == "error"]
        self.assertEqual(len(errors), 4)
        for error in errors:
            self.assertIsInstance(error, RuntimeError)
            self.assertIn("并发名额已满", str(error))
        self.assertEqual(len(self.manager.running), 4)

    def test_unclaimed_task_cannot_start(self):
        task = self.new_task(claim=False)
        with self.assertRaises(ValueError):
            self.manager.start(task, "Read")
        self.assertFalse(self.manager.has_running())

    def test_collect_delivers_once_and_status_query_does_not_consume(self):
        task = self.new_task()
        run_id = self.launch(task)
        self.finish(task, run_id)
        self.assertEqual(self.manager.get(run_id)["status"], "completed")
        self.assertEqual([state.run_id for state in self.manager.collect()], [run_id])
        self.assertEqual(self.manager.collect(), [])
        self.assertEqual(self.manager.get(run_id)["status"], "completed")
        # 运行结束不应自动完成任务看板。
        self.assertEqual(self.board.load_task(task).status, "in_progress")

    def test_cancellation_is_pending_until_publish_and_does_not_affect_peer(self):
        a, b = self.new_task(), self.new_task()
        run_a, run_b = self.launch(a), self.launch(b)
        self.assertEqual(self.manager.cancel(run_a)["status"], "cancelling")
        self.assertEqual(self.manager.get(run_a)["status"], "cancelling")
        self.assertEqual(self.manager.get(run_b)["status"], "running")
        self.assertEqual(self.manager.collect(), [])
        self.assertEqual(self.finish(a, run_a)["status"], "cancelled")
        self.assertEqual(self.finish(b, run_b)["status"], "completed")

    def test_cancel_finished_or_unknown_run_keeps_truthful_status(self):
        task = self.new_task()
        run_id = self.launch(task)
        self.finish(task, run_id)
        self.assertEqual(self.manager.cancel(run_id)["status"], "completed")
        self.assertEqual(self.manager.cancel("unknown")["status"], "not_found")

    def test_worker_failure_publishes_error_and_releases_slot(self):
        task = self.new_task()
        self.executor.errors[task] = ValueError("injected worker failure")
        run_id = self.launch(task)
        result = self.finish(task, run_id)
        self.assertEqual(result["status"], "failed")
        self.assertIn("injected worker failure", result["error"])
        self.assertTrue(result["remaining"])
        self.assertFalse(self.manager.has_running())
        self.assertEqual(len(self.manager.collect()), 1)

    def test_thread_start_failure_rolls_back_registration(self):
        task = self.new_task()
        with patch("CodingAgent.subagent.manager.threading.Thread.start", side_effect=RuntimeError("cannot start")):
            with self.assertRaisesRegex(RuntimeError, "cannot start"):
                self.manager.start(task, "Read")
        self.assertEqual(self.manager.running, {})
        self.assertEqual(self.manager.cancel_events, {})

    def test_shutdown_reports_unfinished_and_rejects_new_work(self):
        task = self.new_task()
        run_id = self.launch(task)
        unfinished = self.manager.shutdown(timeout_seconds=0)
        self.assertIn(run_id, unfinished)
        self.assertEqual(self.manager.get(run_id)["status"], "cancelling")
        with self.assertRaisesRegex(RuntimeError, "关闭"):
            self.manager.start(self.new_task(), "Read")
        self.assertEqual(self.finish(task, run_id)["status"], "cancelled")
