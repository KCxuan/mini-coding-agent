import json
import threading
import time
from pathlib import Path

from ..taskboard import TaskBoard
from .executor import SubagentExecutor
from .state import SubagentState
from .results import (
    finalize_cancelled_subagent,
    format_subagent_result,
)

class SubagentManager:
    def __init__(
        self, 
        task_board: TaskBoard,
        executor: SubagentExecutor,
        *,
        workdir: Path,
        max_workers: int
    ):
        if max_workers < 1:
            raise ValueError("max_workers 必须大于零")

        self.task_board = task_board
        self.executor = executor
        self.workdir = workdir.resolve()
        self.max_workers = max_workers

        # 执行中只公开线程登记，不公开正在修改的状态对象。
        self.running: dict[str, threading.Thread] = {}

        # 执行和收尾完整结束后，才发布状态对象。
        self.results: dict[str, SubagentState] = {}

        # 已完成、尚未领取通知的运行编号。
        self.ready: list[str] = []

        self._lock = threading.Lock()
        self.cancel_events: dict[str, threading.Event] = {}

        self._closing = False

    def start(self, task_id: str, prompt: str) -> str:
        task = self.task_board.load_task(task_id)

        if (
            task.status != "in_progress"
            or task.owner != "agent"
        ):
            raise ValueError(
                "请先认领该任务，再启动 subagent"
            )

        if not prompt.strip():
            raise ValueError("prompt 不能为空")

        state = SubagentState(
            task_id=task_id,
            prompt=prompt,
            workdir=self.workdir,
        )

        thread = threading.Thread(
            target=self._run,
            args=(state,),
            name=state.run_id,
            daemon=True,
        )

        # 检查名额与登记必须在同一个锁内完成。
        with self._lock:
            if self._closing:
                raise RuntimeError(
                    "已开始关闭，不再接受新运行"
                )
            if len(self.running) >= self.max_workers:
                raise RuntimeError(
                    "并发名额已满，"
                    "请等待已有 subagent 返回结果"
                )

            self.running[state.run_id] = thread
            self.cancel_events[state.run_id] = state.cancel_event

            try:
                thread.start()

            except Exception:
                self.running.pop(state.run_id, None)
                self.cancel_events.pop(state.run_id, None)
                raise

        return state.run_id

    def cancel(self, run_id: str) -> dict:
        """发出取消请求，不直接修改执行中的 state。"""
        with self._lock:
            # 已经发布的结果不会被事后改成 cancelled。
            finished = self.results.get(run_id)

            if finished is not None:
                return {
                    "run_id": run_id,
                    "status": finished.status,
                    "message": "这次运行已经结束，保持原结果。",
                }

            event = self.cancel_events.get(run_id)

            if event is None:
                return {
                    "run_id": run_id,
                    "status": "not_found",
                    "error": "当前进程中没有这次运行，请检查 run_id。",
                }

            event.set()

            return {
                "run_id": run_id,
                "status": "cancelling",
                "message": (
                    "已请求取消。当前操作返回或超时后停止后续步骤，"
                    "最终结果将自动交接。"
                ),
            }

    def shutdown(self, timeout_seconds: float = 5.0,) -> list[str]:
        """
        停止接收新运行，通知所有子 Agent 取消，并限时等待。

        返回等待结束时仍未发布结果的 run_id。
        不强制终止线程，也不伪造 cancelled 结果。
        """
        if timeout_seconds < 0:
            raise ValueError("等待时间不能小于零")

        deadline = time.monotonic() + timeout_seconds

        # 锁内：关门、通知取消、复制等待名单。
        with self._lock:
            self._closing = True

            for event in self.cancel_events.values():
                event.set()

            threads = list(self.running.values())

        # 锁外：让子线程能够拿锁并发布结果。
        for thread in threads:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                break

            try:
                # 等待子线程结束
                thread.join(timeout=remaining)

            except RuntimeError:
                # Ctrl+C 极端情况下可能打断线程启动过程。
                # 尚未启动的线程不能 join。
                # 保留登记，稍后如实报告未完成交接。
                continue

        with self._lock:
            return list(self.running)

    def _run(self, state: SubagentState) -> None:
        # 不持锁执行。
        # 模型请求、工具执行和收尾都在这个子线程中完成。
        try:
            self.executor.execute_subagent(state)

        except Exception as exc:
            # 第一阶段通常会自行处理异常。
            # 这里兜住逃出执行函数的异常，
            # 避免一次运行没有交接结果。
            state.status = "failed"
            state.error = (
                f"{type(exc).__name__}: {exc}"
            )
            state.summary = (
                state.summary
                or "子 Agent 意外退出，已有执行记录仍保留。"
            )
            state.remaining = (
                "请主 Agent 检查错误及已有证据后决定如何继续。"
            )

        # 完整执行和收尾都结束后，
        # 一次性发布结果、通知完成并释放名额。
        with self._lock:
            if state.cancel_event.is_set():
                finalize_cancelled_subagent(state)

            self.results[state.run_id] = state
            self.ready.append(state.run_id)
            self.running.pop(state.run_id, None)
            self.cancel_events.pop(state.run_id, None)

        # 从这里开始，工作线程不再修改 state。

    def collect(self) -> list[SubagentState]:
        with self._lock:
            completed = [
                self.results[run_id]
                for run_id in self.ready
            ]
            self.ready.clear()

        return completed

    def has_running(self) -> bool:
        with self._lock:
            return bool(self.running)
    
    def get(self, run_id: str) -> dict:
        """查询快照，不取走 ready 中的完成通知。"""
        with self._lock:
            if run_id in self.running:
                event = self.cancel_events.get(run_id)
                cancelling = (
                    event is not None and event.is_set()
                )

                return {
                    "run_id": run_id,
                    "status": (
                        "cancelling" if cancelling else "running"
                    ),
                    "message": (
                        "已请求取消，正在等待当前操作结束并交接结果。"
                        if cancelling
                        else "子 Agent 正在执行或收尾，最终结果尚未发布。"
                    ),
                }

            state = self.results.get(run_id)

        if state is None:
            return {
                "run_id": run_id,
                "status": "not_found",
                "error": "当前进程中没有这次运行，请检查 run_id。",
            }

        # 发布后工作线程不再修改 state，可以在锁外整理结果。
        # JSON 转换产生新的字典，不把内部 state 直接交出去。
        return json.loads(format_subagent_result(state))