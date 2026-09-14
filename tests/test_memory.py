"""Memory：有效性、去重、召回降级与写入失败后的恢复。"""

import json
from pathlib import Path
from unittest.mock import patch

from CodingAgent.memory.manager import MemoryManager, should_store_memory
from CodingAgent.memory.store import MemoryStore
from tests.helpers import IsolatedTestCase, ScriptedClient, text_response


def record(name="Python preference", **changes):
    item = {"name": name, "type": "user", "scope": "persistent",
            "description": "Preferred Python style", "body": "Use type annotations."}
    item.update(changes)
    return item


class MemoryTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.store = MemoryStore(
            self.workdir / ".memory", self.workdir / ".memory/MEMORY.md", workdir=self.workdir)

    def seed(self):
        self.store.write_memory_file("Python", "user", "Python conventions", "Use Python annotations.")
        self.store.write_memory_file("Review", "feedback", "Review conventions", "Explain findings.")

    def snapshot(self):
        return {item["filename"]: self.store.read_memory_file(item["filename"])
                for item in self.store.list_memory_files()}

    def test_store_rebuilds_index_and_can_reload(self):
        self.store.write_memory_file("中文偏好", "user", "回答语言", "使用中文解释。")
        records = self.store.list_memory_files()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["body"], "使用中文解释。")
        self.assertIn(records[0]["filename"], self.store.read_memory_index())

    def test_invalid_type_and_empty_content_do_not_create_records(self):
        for name, kind, description, body in (
            ("name", "invalid", "desc", "body"), ("", "user", "desc", "body"),
            ("name", "user", "", "body"), ("name", "user", "desc", ""),
        ):
            with self.subTest(name=name, kind=kind, description=description, body=body):
                with self.assertRaises(ValueError):
                    self.store.write_memory_file(name, kind, description, body)
        self.assertEqual(self.store.list_memory_files(), [])

    def test_memory_paths_cannot_escape_or_read_index_as_record(self):
        for filename in ("../outside.md", str(self.root / "outside.md"), "MEMORY.md"):
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    self.store.memory_path(filename)

    def test_temporary_or_current_task_memory_is_rejected(self):
        self.assertFalse(should_store_memory(record(scope="current_task"), []))
        self.assertFalse(should_store_memory(record(body="本次任务只阅读文件"), []))
        self.assertTrue(should_store_memory(record(), []))

    def test_duplicate_name_description_or_body_is_rejected(self):
        original = record()
        for candidate in (
            record(description="Different", body="Different"),
            record(name="Other", body="Different"),
            record(name="Other", description="Different"),
        ):
            with self.subTest(candidate=candidate):
                self.assertFalse(should_store_memory(candidate, [original]))

    def test_extraction_deduplicates_candidates_in_same_response(self):
        candidate = record()
        client = ScriptedClient(text_response(json.dumps([candidate, candidate])))
        manager = MemoryManager(client, "offline-model", self.store)
        count = manager.extract_memories([{"role": "user", "content": "I prefer annotations"}])
        self.assertEqual(count, 1)
        self.assertEqual(len(self.store.list_memory_files()), 1)

    def test_recall_model_error_falls_back_to_keyword_selection(self):
        self.seed()
        manager = MemoryManager(ScriptedClient(RuntimeError("offline error")), "offline-model", self.store)
        selected = manager.select_relevent_memory([{"role": "user", "content": "Python"}])
        self.assertEqual(selected, ["python.md"])

    def test_recall_respects_content_character_budget(self):
        self.seed()
        manager = MemoryManager(ScriptedClient(), "offline-model", self.store, recall_char_limit=20)
        with patch.object(manager, "select_relevent_memory", return_value=["python.md", "review.md"]):
            recalled = json.loads(manager.load_memories([]))
        self.assertEqual(sum(len(item["content"]) for item in recalled), 20)
        self.assertEqual(recalled[0]["source"], "python.md")

    def test_partial_replacement_failure_restores_original_files_and_index(self):
        self.seed()
        originals = self.snapshot()
        index_lines = set(self.store.read_memory_index().splitlines())
        records = self.store.list_memory_files()
        consolidated = [record("New first"), record("New second")]
        original_write = Path.write_text
        failed = []

        def fail_second_once(path, content, *args, **kwargs):
            if path.name == "new-second.md" and not failed:
                failed.append(path)
                raise OSError("injected disk write failure")
            return original_write(path, content, *args, **kwargs)

        with patch.object(Path, "write_text", new=fail_second_once):
            with self.assertRaisesRegex(OSError, "injected disk write failure"):
                self.store.replace_records(records, consolidated)
        self.assertEqual(len(failed), 1)
        self.assertEqual(self.snapshot(), originals)
        self.assertEqual(set(self.store.read_memory_index().splitlines()), index_lines)
        self.assertFalse((self.store.directory / "new-first.md").exists())

    def test_empty_or_colliding_consolidation_does_not_replace_records(self):
        self.seed()
        before = self.snapshot()
        for response in ([], [record("same-name"), record("same name")]):
            with self.subTest(response=response):
                client = ScriptedClient(text_response(json.dumps(response)))
                manager = MemoryManager(client, "offline-model", self.store, consolidate_threshold=1)
                with patch.object(self.store, "replace_records", wraps=self.store.replace_records) as replace:
                    self.assertEqual(manager.consolidate_memories(), 0)
                    replace.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_valid_consolidation_replaces_records_and_rebuilds_index(self):
        self.seed()
        client = ScriptedClient(text_response(json.dumps([record("Combined preference")])))
        manager = MemoryManager(client, "offline-model", self.store, consolidate_threshold=1)
        self.assertEqual(manager.consolidate_memories(), 1)
        self.assertEqual([r["filename"] for r in self.store.list_memory_files()], ["combined-preference.md"])
        self.assertIn("combined-preference.md", self.store.read_memory_index())
        self.assertNotIn("python.md", self.store.read_memory_index())
