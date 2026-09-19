from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4


_IS_WINDOWS = sys.platform == "win32"


def format_bash_output(output: str, exit_code: int | None) -> str:
    if exit_code in (0, None):
        return output
    return f"Error: Command exited with code {exit_code}\n{output}"


class ShellRunner:
    def __init__(self, workdir: Path, *, output_dir: Path | None = None):
        self.workdir = workdir.resolve()
        self.output_dir = (
            output_dir
            if output_dir is not None
            else self.workdir / ".task_outputs" / "tool-results"
        ).resolve()
        self._processes: set[subprocess.Popen] = set()
        self._lock = threading.RLock()
    
    def prepare_output(
        self,
        output: str,
        *,
        inline_limit: int = 500,
    ) -> tuple[str, str | None]:
        """
        返回：(要交给模型的正文或预览, 全文路径)。
        短输出：直接返回全文，不创建文件。
        长输出：先保存全文，再返回最多 500 字符的首尾预览。
        """
        if len(output) <= inline_limit:
            return output, None
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.output_dir / f"shell_{uuid4().hex}.txt"
            with path.open("x", encoding="utf-8", newline="") as file:
                file.write(output)

        except OSError as error:
            # 保存失败时，不返回虚假的路径，也不截掉原输出。
            # 这次退回全文交付，并明确说明原因。
            return (
                f"[output archive failed] {error}\n"
                f"以下为未截断的输出：\n{output}",
                None,
            )

        # 包括省略提示在内，预览最多 500 字符。
        marker = "\n... [中间内容已省略，请读取全文] ...\n"
        remaining = 500 - len(marker)
        head_size = (remaining + 1) // 2
        tail_size = remaining // 2

        preview = (
            output[:head_size]
            + marker
            + output[-tail_size:]
        )

        return preview, str(path)

    def _stop_process_group(self, process: subprocess.Popen):
        """停止一个进程组及其所有子进程"""
        if process.poll() is not None:
            # poll() 返回非None，表示进程已结束
            return
        
        if _IS_WINDOWS:
            # windows 没有killpg 对Popen对象本身进行terminate/kill
            for sig_fn in (process.terminate, process.kill):
                try:
                    sig_fn()
                except OSError:
                    pass
                if process.poll() is not None:
                    return
                time.sleep(0.05)
        else:
            # linux/macos 使用killpg停止进程组
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except (OSError, ProcessLookupError):
                    return
                if process.poll() is not None:
                    return
                time.sleep(0.05)


    def stop_all_shell_processes(self):
        """停止所有shell进程"""
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            self._stop_process_group(process)

    def _popen_kwargs(self) -> dict:
        """按照不同的平台返回 subprocess.Popen 的 kwargs"""
        kwargs: dict = {
            "shell": True,
            "cwd": self.workdir,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "errors": "replace",
        }
        if _IS_WINDOWS:
            # windows 新进程组，便于后续terminate时不误伤agent自身
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # linux/macos 独立session, 可以killpg
            kwargs["start_new_session"] = True
        return kwargs

    def run_bash_process(self, command: str, *, timeout=120.0) -> tuple[str, int | None]:
        """
        运行一个bash命令，返回输出和退出码
        """
        process: subprocess.Popen | None = None
        try:
            process = subprocess.Popen(command, **self._popen_kwargs())
            with self._lock:
                self._processes.add(process)
            stdout, stderr = process.communicate(timeout=timeout)
            output = (stdout + stderr).strip()
            return (output if output else "(no output)", process.returncode)
        except subprocess.TimeoutExpired:
            return "Error: Command timed out.", None
        except OSError as e:
            return f"Error: {e}", None
        finally:
            if process:
                self._stop_process_group(process)
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
                with self._lock:
                    self._processes.discard(process)

    def run_bash(
        self,
        command: str,
        run_in_background: bool = False,
    ) -> str:
        output, exitcode = self.run_bash_process(command)
        result = format_bash_output(output, exitcode)

        preview, full_path = self.prepare_output(result, inline_limit=50000)

        if full_path is None:
            return preview

        # 使用现有压缩模块能识别的存档格式。
        return (
            "<persisted-output>\n"
            f"Full output: {full_path}\n"
            "Preview:\n"
            f"{preview}\n"
            "需要完整详情时，请使用 read_file 读取上述文件。\n"
            "</persisted-output>"
        )
