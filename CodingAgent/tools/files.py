from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from ..images import ToolContent, read_image

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

    def run_grep(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        ignore_case: bool = False,
        limit: int = 100,
    ) -> str:
        """在工作目录内搜索普通文本，返回匹配行及位置。"""
        try:
            # 1. 检查参数。
            if not isinstance(pattern, str) or not pattern:
                raise ValueError("pattern 必须是非空字符串")

            if any(char in pattern for char in ("\n", "\r", "\0")):
                raise ValueError(
                    "pattern 必须是单行文本，且不能包含空字符"
                )

            if not isinstance(path, str) or not path:
                raise ValueError("path 必须是非空字符串")

            if glob is not None and (
                not isinstance(glob, str) or not glob
            ):
                raise ValueError("glob 必须是非空字符串或 null")

            if type(ignore_case) is not bool:
                raise ValueError("ignore_case 必须是布尔值")

            if type(limit) is not int or not 1 <= limit <= 200:
                raise ValueError("limit 必须是 1 到 200 的整数")

            target = self.safe_path(path)

            if not target.exists():
                raise ValueError("搜索路径不存在")

            if not (target.is_file() or target.is_dir()):
                raise ValueError("搜索路径必须是文件或目录")

            executable = shutil.which("rg")
            if executable is None:
                return "Error: 未找到 rg，请安装 ripgrep 并加入 PATH。"

            # 2. 构造命令。参数列表不会经过 Shell 解释。
            relative = target.relative_to(self.workdir).as_posix()

            command = [
                executable,
                "--no-config",
                "--no-follow",
                "--fixed-strings",
                "--line-number",
                "--with-filename",
                "--no-heading",
                "--color=never",
                "--line-buffered",
                "--max-columns=500",
                "--max-columns-preview",
                "--ignore-case" if ignore_case else "--case-sensitive",
            ]

            if glob is not None:
                command.extend(["--glob", glob])

            # -e 后面是搜索文本；-- 后面是搜索路径。
            command.extend(["-e", pattern, "--", relative])

            matches = []
            truncated = False
            timed_out = threading.Event()

            # 3. 执行搜索，逐行收集。
            # 错误输出单独保存，避免混入匹配结果或堵塞管道。
            with tempfile.TemporaryFile() as errors:
                with subprocess.Popen(
                    command,
                    cwd=self.workdir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                    creationflags=getattr(
                        subprocess, "CREATE_NO_WINDOW", 0
                    ),
                ) as process:

                    def stop_process():
                        if process.poll() is None:
                            try:
                                process.kill()
                            except OSError:
                                pass

                    def on_timeout():
                        if process.poll() is None:
                            timed_out.set()
                            stop_process()

                    timer = threading.Timer(15.0, on_timeout)
                    timer.daemon = True
                    timer.start()

                    try:
                        for line in process.stdout:
                            # 多读到一条，才确定还有结果未返回。
                            if len(matches) >= limit:
                                truncated = True
                                break

                            matches.append(line.rstrip("\r\n"))

                        if truncated:
                            stop_process()

                        return_code = process.wait()

                    finally:
                        timer.cancel()
                        stop_process()
                        process.wait()
                        timer.join()

                errors.seek(0)
                error_text = (
                    errors.read(2000)
                    .decode("utf-8", errors="replace")
                    .strip()
                )

            # 4. 区分完整结果、截断、超时和错误。
            if timed_out.is_set():
                status = (
                    "Error: 搜索超过 15 秒，结果可能不完整，"
                    "请缩小范围。"
                )

            elif truncated:
                status = (
                    "Truncated: true；请缩小 path、glob "
                    "或使用更具体的 pattern。"
                )
                if error_text:
                    status += f"\nSearch warning: {error_text}"

            elif return_code not in (0, 1):
                status = (
                    f"Error: rg 搜索失败，退出码 {return_code}；"
                    f"结果可能不完整。\n{error_text}"
                )

            else:
                status = "Truncated: false"

            body = "\n".join(matches)

            if not body:
                body = (
                    "(no matches)"
                    if return_code in (0, 1) and not timed_out.is_set()
                    else "(no results collected)"
                )

            return (
                f"Search: {pattern}\n"
                f"Scope: {relative}\n"
                f"Files: {glob or '(default rg filters)'}\n\n"
                f"{body}\n\n"
                f"Returned: {len(matches)} matching lines\n"
                f"{status}"
            )

        except (OSError, ValueError, RuntimeError) as error:
            return f"Error: {error}"

    def run_read_image(self, path: str) -> ToolContent:
        """读取工作目录内的图片，返回视觉输入内容"""
        try:
            return read_image(self.safe_path(path))
        except Exception as e:
            return f"Error: {e}"