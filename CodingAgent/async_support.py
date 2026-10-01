# 线程适配、输入、权限和工具分发
import asyncio
import inspect
import threading

from prompt_toolkit import PromptSession

from .background import should_run_background
from .hooks import HookRegistry
from .permissions import PermissionManager, check_deny_list


async def finish_task(task):
    """
    等待任务实际结束。

    外层收到取消时，暂缓向外抛出 CancelledError，
    等内部任务完成后再传递取消。

    用于不能直接中止的同步工作，以及退出清理。
    """
    cancellation_requested = False

    while True:
        try:
            result = await asyncio.shield(task)

        except asyncio.CancelledError:
            if task.cancelled():
                raise

            cancellation_requested = True
            continue

        except Exception:
            if cancellation_requested:
                raise asyncio.CancelledError() from None
            raise

        else:
            if cancellation_requested:
                raise asyncio.CancelledError()
            return result


async def run_sync(func, /, *args, **kwargs):
    """在线程池执行同步函数，并等待它实际完成。"""
    task = asyncio.create_task(
        asyncio.to_thread(func, *args, **kwargs)
    )
    return await finish_task(task)


class AsyncConsole:
    """创建一个终端输入对象；每次需要输入时，
    先保证没有其他提示正在读取键盘，再异步等待用户回答"""
    def __init__(self):
        self._session = PromptSession()
        self._input_lock = asyncio.Lock()

    async def read(self, prompt):
        # 同一时刻只允许一个输入提示。
        async with self._input_lock:
            return await self._session.prompt_async(prompt)

class AsyncHookRegistry(HookRegistry):
    async def trigger_async(self, hook_name, *args):
        for callback in self._hooks[hook_name]:
            result = callback(*args)

            if inspect.isawaitable(result):
                result = await result

            # 保持原 HookRegistry 的短路规则。
            if result is not None:
                return result

        return None

class AsyncPermissionManager(PermissionManager):
    def __init__(
        self,
        workdir,
        *,
        get_mcp_policy,
        console,
    ):
        super().__init__(
            workdir,
            get_mcp_policy=get_mcp_policy,
        )
        self.console = console

    async def ask_user(self, tool_name, args, reason):
        # 延续现有规则：后台线程不得直接发起终端交互。
        if threading.current_thread() is not threading.main_thread():
            return "deny"

        print(f"\n\033[33m⚠  {reason}\033[0m")
        print(f"\033[33m   Tool: {tool_name}({args})\033[0m")

        try:
            choice = await self.console.read("   Allow? [y/N] ")
        except EOFError:
            return "deny"

        return (
            "allow"
            if choice.strip().lower() in ("y", "yes")
            else "deny"
        )

    async def check_permission_async(self, block):
        if block.name == "bash":
            reason = check_deny_list(
                block.input.get("command", "")
            )
            if reason:
                print(f"\n\033[31m⛔ {reason}\033[0m")
                return reason

        if block.name in {
            "bash",
            "read_file",
            "write_file",
            "edit_file",
        }:
            reason = self.check_rules(
                block.name,
                block.input,
            )
            if reason:
                decision = await self.ask_user(
                    block.name,
                    block.input,
                    reason,
                )
                if decision == "deny":
                    return reason

        if block.name.startswith("mcp__"):
            policy = self.get_mcp_policy(block.name)

            if policy != "allow":
                decision = await self.ask_user(
                    block.name,
                    block.input,
                    "External MCP tool",
                )
                if decision == "deny":
                    return "Permission denied by user"

        return None

class AsyncToolDispatcher:
    # 这些同步工具可能较耗时，执行主体交给工作线程。
    THREADED_TOOLS = {
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "load_skill",
    }

    def __init__(self, hooks, background):
        self.hooks = hooks
        self.background = background

    async def execute_tool(
        self,
        tool_call,
        handlers,
        *,
        allow_background=True,
    ):
        blocked = await self.hooks.trigger_async(
            "PreToolUse",
            tool_call,
        )
        if blocked:
            return str(blocked)

        try:
            if allow_background and should_run_background(
                tool_call.name,
                tool_call.input,
            ):
                # 审批通过后，仍由原管理器启动后台命令。
                task_id = self.background.start(tool_call)
                output = (
                    f"Background task started: {task_id}\n"
                    "The result will be collected and injected "
                    "into the messages later."
                )

            else:
                handler = handlers.get(tool_call.name)

                if handler is None:
                    output = f"Unknown: {tool_call.name}"

                elif tool_call.name in self.THREADED_TOOLS:
                    output = await run_sync(
                        handler,
                        **tool_call.input,
                    )

                else:
                    # 包括原有 Subagent 管理接口、
                    # 任务板接口，以及异步 MCP handler。
                    output = handler(**tool_call.input)

                    # 不能只检查 handler 是否为 async def：
                    # 原 MCP 工具池中的 lambda 也会返回协程。
                    if inspect.isawaitable(output):
                        output = await output

        except Exception as exc:
            output = f"Error: {exc}"

        await self.hooks.trigger_async(
            "PostToolUse",
            tool_call,
            output,
        )
        return str(output)