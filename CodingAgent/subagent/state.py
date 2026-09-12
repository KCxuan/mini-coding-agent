import threading
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

@dataclass
class SubagentState:
    task_id: str
    prompt: str
    workdir: Path
    
    run_id: str = field(
        default_factory=lambda: f"run_{uuid4().hex}"
    )

    
    max_turns: int = 30
    timeout_seconds: float = 600
    summary_timeout_seconds: float = 20

    # 执行过程
    status: str = "running"
    turns_used: int = 0
    messages: list[dict] = field(default_factory=list)
    response_log: list[dict] = field(default_factory=list)
    # 每次运行有自己的取消信号，取消 A 不会影响 B。
    cancel_event: threading.Event = field(
        default_factory=threading.Event,
        repr=False,
        compare=False,
    )

    # 最终交接
    summary: str = ""
    remaining: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

class SubagentCancelled(Exception):
    """通过异常退出当前子循环，不代表工具执行失败。"""


def check_subagent_cancelled(state: SubagentState) -> None:
    if state.cancel_event.is_set():
        raise SubagentCancelled("收到取消请求")