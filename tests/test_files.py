"""文件工具：分页、参数、越界和实际磁盘内容。"""

from CodingAgent.tools.files import FileTools
from tests.helpers import IsolatedTestCase


class FileToolsTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.files = FileTools(self.workdir)
        self.source = self.workdir / "sample.txt"
        self.source.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")

    def test_read_middle_page(self):
        result = self.files.run_read_file("sample.txt", offset=2, limit=2)
        self.assertIn("2: beta\n3: gamma", result)
        self.assertIn("Next offset: 4", result)
        self.assertNotIn("1: alpha", result)
        self.assertNotIn("4: delta", result)

    def test_read_last_page_reports_eof(self):
        result = self.files.run_read_file("sample.txt", offset=4, limit=2)
        self.assertIn("4: delta", result)
        self.assertIn("[EOF]", result)
        self.assertNotIn("Next offset:", result)

    def test_empty_file_and_offset_past_end(self):
        (self.workdir / "empty.txt").write_text("", encoding="utf-8")
        for path, offset, total in (("empty.txt", 1, 0), ("sample.txt", 5, 4)):
            with self.subTest(path=path):
                result = self.files.run_read_file(path, offset=offset)
                self.assertIn("[EOF]", result)
                self.assertIn(f"Total lines: {total}", result)

    def test_read_limit_is_capped_at_200(self):
        self.source.write_text("\n".join(map(str, range(1, 252))), encoding="utf-8")
        result = self.files.run_read_file("sample.txt", limit=999)
        self.assertIn("200: 200", result)
        self.assertNotIn("201: 201", result)
        self.assertIn("Next offset: 201", result)

    def test_invalid_paging_arguments_return_errors(self):
        for field in ("offset", "limit"):
            for value in (0, -1, True, 1.5, "2"):
                with self.subTest(field=field, value=value):
                    self.assertTrue(self.files.run_read_file(
                        "sample.txt", **{field: value}).startswith("Error:"))

    def test_missing_file_returns_error(self):
        self.assertTrue(self.files.run_read_file("missing.txt").startswith("Error:"))

    def test_write_creates_parents_and_preserves_unicode(self):
        result = self.files.run_write_file("nested/new.txt", "中文\nhello")
        self.assertFalse(result.startswith("Error:"))
        self.assertEqual((self.workdir / "nested/new.txt").read_text(
            encoding="utf-8"), "中文\nhello")

    def test_edit_replaces_only_first_match(self):
        self.source.write_text("old old", encoding="utf-8")
        self.files.run_edit_file("sample.txt", "old", "new")
        self.assertEqual(self.source.read_text(encoding="utf-8"), "new old")

    def test_edit_missing_match_keeps_file_unchanged(self):
        before = self.source.read_bytes()
        result = self.files.run_edit_file("sample.txt", "absent", "replacement")
        self.assertTrue(result.startswith("Error:"))
        self.assertEqual(self.source.read_bytes(), before)

    def test_traversal_and_absolute_outside_paths_cannot_read_or_write(self):
        outside = self.root / "outside.txt"
        outside.write_text("private sentinel", encoding="utf-8")
        for path in ("../outside.txt", str(outside)):
            with self.subTest(path=path):
                read = self.files.run_read_file(path)
                self.assertTrue(read.startswith("Error:"))
                self.assertNotIn("private sentinel", read)
                self.assertTrue(self.files.run_write_file(path, "changed").startswith("Error:"))
                self.assertTrue(self.files.run_edit_file(path, "private", "public").startswith("Error:"))
                self.assertEqual(outside.read_text(encoding="utf-8"), "private sentinel")

    def test_glob_excludes_outside_matches(self):
        (self.root / "outside.txt").write_text("outside", encoding="utf-8")
        self.assertEqual(self.files.run_glob("../*.txt"), "(no matches)")
        self.assertIn("sample.txt", self.files.run_glob("**/*.txt"))
