from collections.abc import Callable
from pathlib import Path


class HookRegistry:
    def __init__(self):
        self._hooks: dict[str, list[Callable]] = {
            "UserPromptSubmit": [],
            "PreToolUse": [],
            "PostToolUse": [],
            "Stop": [],
        }

    def register(self, hook_name: str, hook_func: Callable):
        self._hooks[hook_name].append(hook_func)

    def trigger(self, hook_name: str, *args):
        for callback in self._hooks[hook_name]:
            result = callback(*args)
            if result is not None:
                return result
        return None


class DefaultHooks:
    def __init__(self, workdir: Path):
        self.workdir = workdir
    
    def context_inject_hook(self, query: str) -> str | None:
        """Inject current working directory info into every prompt."""
        print(f"\033[90m[HOOK] UserPromptSubmit: working in {self.workdir}\033[0m")
        return None   # return None = no modification, let prompt through

    # PreToolUse: 日志
    def log_hook(self, block):
        print(f"\033[90m[HOOK] {block.name}(...)\033[0m")

    # PostToolUse: 大文件提醒
    def large_output_hook(self, block, output):
        if len(str(output)) > 100000:
            print(f"\033[33m[HOOK] ⚠ Large output from {block.name}\033[0m")

    # Stop: 退出总结实际调用工具的次数
    def summary_hook(self, messages: list[dict], tool_call_count: int) -> str | None:
        print(f"\033[90m[HOOK] 本Agent工具调用次数: {tool_call_count}\033[0m")
        return None