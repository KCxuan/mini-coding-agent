"""任务依赖和状态必须通过持久化记录验证。"""

from CodingAgent.taskboard import TaskBoard, TaskStore
from tests.helpers import IsolatedTestCase


class TaskBoardTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.store = TaskStore(self.workdir / "tasks", workdir=self.workdir)
        self.board = TaskBoard(self.store)

    def test_create_and_reload_from_new_store(self):
        task = self.board.create_task("分析代码", "阅读入口")
        reloaded = TaskStore(self.workdir / "tasks", workdir=self.workdir).load(task.id)
        self.assertEqual(reloaded, task)
        self.assertEqual(reloaded.status, "pending")
        self.assertIsNone(reloaded.owner)

    def test_dependency_blocks_claim_until_predecessor_completed(self):
        first = self.board.create_task("A")
        second = self.board.create_task("B")
        self.board.update_task(second.id, [first.id])
        self.board.claim_task(second.id)
        self.assertEqual(self.store.load(second.id).status, "pending")
        self.board.claim_task(first.id)
        result = self.board.complete_task(first.id)
        self.assertIn("Unblocked: B", result)
        self.board.claim_task(second.id)
        self.assertEqual(self.store.load(second.id).status, "in_progress")

    def test_multiple_dependencies_must_all_complete(self):
        a, b, c = [self.board.create_task(name) for name in ("A", "B", "C")]
        self.board.update_task(c.id, [a.id, b.id])
        self.board.claim_task(a.id)
        self.board.complete_task(a.id)
        self.assertFalse(self.board.can_start(c.id))
        self.board.claim_task(b.id)
        self.board.complete_task(b.id)
        self.assertTrue(self.board.can_start(c.id))

    def test_transitive_cycle_rejected_without_changing_dependencies(self):
        a, b, c = [self.board.create_task(name) for name in ("A", "B", "C")]
        self.board.update_task(b.id, [a.id])
        self.board.update_task(c.id, [b.id])
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.board.update_task(a.id, [c.id])
        self.assertEqual(self.store.load(a.id).blockedBy, [])

    def test_self_and_missing_dependencies_are_rejected(self):
        task = self.board.create_task("A")
        for dependency in (task.id, "task_missing"):
            with self.subTest(dependency=dependency):
                with self.assertRaises(ValueError):
                    self.board.update_task(task.id, [dependency])
                self.assertEqual(self.store.load(task.id).blockedBy, [])

    def test_duplicate_dependencies_are_stored_once(self):
        a, b = [self.board.create_task(name) for name in ("A", "B")]
        self.board.update_task(b.id, [a.id, a.id])
        self.board.update_task(b.id, [a.id])
        self.assertEqual(self.store.load(b.id).blockedBy, [a.id])

    def test_repeated_claim_cannot_change_owner(self):
        task = self.board.create_task("A")
        self.board.claim_task(task.id, owner="alice")
        self.board.claim_task(task.id, owner="bob")
        self.assertEqual(self.store.load(task.id).owner, "alice")

    def test_wrong_owner_or_unclaimed_task_cannot_complete(self):
        task = self.board.create_task("A")
        self.board.complete_task(task.id)
        self.assertEqual(self.store.load(task.id).status, "pending")
        self.board.claim_task(task.id, owner="alice")
        self.board.complete_task(task.id, owner="bob")
        self.assertEqual(self.store.load(task.id).status, "in_progress")

    def test_claimed_task_dependencies_cannot_be_changed(self):
        a, b = [self.board.create_task(name) for name in ("A", "B")]
        self.board.claim_task(b.id)
        with self.assertRaises(ValueError):
            self.board.update_task(b.id, [a.id])
        self.assertEqual(self.store.load(b.id).blockedBy, [])

    def test_task_id_cannot_escape_store(self):
        with self.assertRaises(ValueError):
            self.store.load("../../outside")
