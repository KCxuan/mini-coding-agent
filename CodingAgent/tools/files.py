from pathlib import Path


class FileTools:
    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()

    # 定义安全path函数
    def safe_path(self, p: str) -> str:
        path = (self.workdir / p).resolve()
        if not path.is_relative_to(self.workdir):
            raise ValueError("Error: Path escapes the working directory.")
        return path

    # 定义阅读文档的工具执行函数(read_file)
    def run_read_file(
        self,
        path: str,
        limit: int | None = 200,
        offset: int = 1,
    ) -> str:
        """按 1-based 起始行分页；保留第二个位置参数 limit 的兼容性。"""
        try:
            if type(offset) is not int or offset < 1:
                raise ValueError("offset 必须是大于零的整数（起始行号）")
            if limit is None:
                limit = 200
            if type(limit) is not int or limit < 1:
                raise ValueError("limit 必须是大于零的整数")

            lines = self.safe_path(path).read_text(encoding="utf-8").splitlines()
            total = len(lines)
            if offset > total:
                return f"File: {path}\n[EOF] Total lines: {total}; requested offset: {offset}"

            end = min(offset - 1 + min(limit, 200), total)
            page = [
                f"{number}: {lines[number - 1]}"
                for number in range(offset, end + 1)
            ]
            footer = f"Next offset: {end + 1}" if end < total else "[EOF]"
            return "\n".join([
                f"File: {path}",
                f"Lines {offset}-{end} of {total}",
                *page,
                footer,
            ])
        except (ValueError, OSError) as exc:
            return f"Error: {exc}"

    # 定义写入文档的工具执行函数(write_file)
    def run_write_file(self, path: str, content: str) -> str:
        try:
            file_path = self.safe_path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error: {e}"

    # 定义编辑文档的工具执行函数(edit_file)
    def run_edit_file(self, path: str, old_content: str, new_content: str) -> str:
        try:
            file_path = self.safe_path(path)
            content = file_path.read_text(encoding="utf-8")
            if old_content not in content:
                return f"Error: {old_content} not found in {path}"
            file_path.write_text(content.replace(old_content, new_content, 1), encoding="utf-8")
            return f"Edited {path}"
        except Exception as e:
            return f"Error: {e}"

    # 定义搜索文档的工具执行函数(glob)
    def run_glob(self, pattern: str) -> str:
        import glob as g
        try:
            matches = sorted({
                match for match in g.glob(
                    pattern, root_dir=self.workdir, recursive=True)
                if (self.workdir / match).resolve().is_relative_to(self.workdir)
            })
            shown = matches[:200]
            if len(matches) > 200:
                shown.append("... (more matches omitted; narrow the pattern)")
            return "\n".join(shown) if shown else "(no matches)"
        except Exception as e:
            return f"Error: {e}"