from dataclasses import dataclass
from pathlib import Path
import os


"""
下面有两个写法要注意
frozen=True 表示这个类初始化后是不可变的，不能被修改
@property 表示可以像访问属性一样访问这个方法
"""

@dataclass(frozen=True)
class AgentConfig:
    # 工作目录
    workdir: Path
    # 模型
    model: str | None
    # 最大子代理数
    max_subagents: int = 4

    @property
    def skills_dir(self) -> Path:
        """技能目录"""
        return self.workdir / "skills"

    @property
    def transcript_dir(self) -> Path:
        """转录目录"""
        return self.workdir / ".transcripts"

    @property
    def tool_results_dir(self) -> Path:
        """工具结果目录"""
        return self.workdir / ".task_outputs" / "tool-results"

    @property
    def memory_dir(self) -> Path:
        """记忆目录"""
        return self.workdir / ".memory"

    @property
    def memory_index(self) -> Path:
        """记忆索引"""
        return self.memory_dir / "MEMORY.md"

    @property
    def task_dir(self) -> Path:
        """任务目录"""
        return self.workdir / "tasks"

    @property
    def mcp_config_path(self) -> Path:
        """MCP配置文件路径"""
        return self.workdir / "mcp.json"


def load_config() -> AgentConfig:
    return AgentConfig(
        workdir=Path.cwd(),
        model=os.getenv("ANTHROPIC_MODEL"),
    )