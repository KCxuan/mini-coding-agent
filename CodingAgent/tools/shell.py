from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


_IS_WINDOWS = sys.platform == "win32"


def format_bash_output(output: str, exit_code: int | None) -> str:
    if exit_code in (0, None):
        return output
    return f"Error: Command exited with code {exit_code}\n{output}"


class ShellRunner:
    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self._processes: set[subprocess.Popen] = set()
        self._lock = threading.RLock()

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
            if len(output) > 50000:
                output = output[:50000]
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
        return format_bash_output(*self.run_bash_process(command))