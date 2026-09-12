from __future__ import annotations
import os
import dotenv
import anthropic
import signal
import time
import atexit
import sys
try:
    import readline
except ImportError:
    pass


# ----------------------------------------------------

from CodingAgent.skill_loader import SkillLoader

from CodingAgent.config import load_config

from CodingAgent.prompts import build_subagent_prompt, build_system_prompt

from CodingAgent.taskboard import TaskBoard, TaskStore
from CodingAgent.todo import TODOManager

from CodingAgent.tools.files import FileTools

from CodingAgent.compact import ContextCompactor

from CodingAgent.hooks import HookRegistry, DefaultHooks
from CodingAgent.permissions import PermissionManager

from CodingAgent.memory.store import MemoryStore
from CodingAgent.memory.manager import MemoryManager

from CodingAgent.mcp.config import MCPServerConfig, load_mcp_config
from CodingAgent.mcp.bridge import AsyncBridge
from CodingAgent.mcp.client import MCPClient
from CodingAgent.mcp.manager import MCPManager

from CodingAgent.tools.shell import ShellRunner, format_bash_output

from CodingAgent.background import (
    BackgroundManager
)

from CodingAgent.subagent.results import (
    format_subagent_result,
)

from CodingAgent.messages import extract_text

from CodingAgent.subagent.executor import (
    SUB_READONLY_TOOL_NAMES,
    SubagentExecutor,
)

from CodingAgent.subagent.manager import SubagentManager

from CodingAgent.tools.schemas import build_tool_schemas

from CodingAgent.tools.adapters import SubagentTools, run_compact
from CodingAgent.tools.dispatcher import ToolDispatcher

dotenv.load_dotenv()
CONFIG = load_config()

WORKDIR = CONFIG.workdir
SKILLS_DIR = CONFIG.skills_dir
TRANSCRIPT_DIR = CONFIG.transcript_dir
TOOL_RESULTS_DIR = CONFIG.tool_results_dir
MEMORY_DIR = CONFIG.memory_dir
MEMORY_INDEX = CONFIG.memory_index
MCP_CONFIG_PATH = CONFIG.mcp_config_path
TASK_DIR = CONFIG.task_dir
# 定义Anthropic客户端
client = anthropic.Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"), 
    base_url=os.getenv("ANTHROPIC_BASE_URL")
)

MODEL = CONFIG.model # default model == deepseek-flash

# 子 Agent 同时运行的数量上限；提示词与管理器共用。
MAX_SUBAGENTS = CONFIG.max_subagents

# -------------- 技能加载器 --------------
SKILL_LOADER = SkillLoader(SKILLS_DIR)

SUB_SYSTEM_PROMPT = build_subagent_prompt(WORKDIR, SKILL_LOADER.catalog())

# 定义工具列表

TOOLS = build_tool_schemas(
    max_subagents=MAX_SUBAGENTS,
)

SUB_TOOLS = [
    tool
    for tool in TOOLS
    if tool["name"] in SUB_READONLY_TOOL_NAMES
]




# 保留历史 TODO 实现；当前 Agent 使用 task 系统，未注册 todo_write 工具。
TODO = TODOManager()
run_todo_write = TODO.run_todo_write


# ------------- 带有后台跑命令的bash工具 -------------

SHELL = ShellRunner(WORKDIR)

# 防止重复 Ctrl+C 打断正在进行的清理。
_exit_requested = False
_cleanup_started = False

def _handle_termination_signal(signum, _frame):
    """只发起退出，不在信号处理函数中执行资源清理。"""
    global _exit_requested

    if _exit_requested or _cleanup_started:
        return

    _exit_requested = True
    raise SystemExit(128 + signum)


if sys.platform == "win32":
    signal.signal(signal.SIGINT, _handle_termination_signal)
else:
    signal.signal(signal.SIGTERM, _handle_termination_signal)
    signal.signal(signal.SIGINT, _handle_termination_signal)

# ------此部分是 background tasks 的实现代码 ---------

BACKGROUND = BackgroundManager(SHELL)

# -------------- 这一部分是MCP的实现代码 ------------

try:
    MCP_CONFIG = load_mcp_config(MCP_CONFIG_PATH)
except (FileNotFoundError, ValueError) as e:
    print(f"Error loading MCP config: {e}")
    MCP_CONFIG = {}

MCP_HOST_POLICY = {
    ("fetch", "fetch"): "confirm",
}

MCP_BRIDGE = AsyncBridge()

MCP_MANAGER = MCPManager(
    MCP_CONFIG,
    MCP_BRIDGE,
    host_policy=MCP_HOST_POLICY,
)



# --------------------------------------------------


FILES = FileTools(WORKDIR)




# -----------------定义异步结果注入函数-----------------
def inject_async_results(
    messages: list[dict],
) -> int:
    shell_count = BACKGROUND.inject_background_results(
        messages
    )
    subagent_count = SUBAGENT_TOOLS.inject_subagent_results(
        messages
    )

    return shell_count + subagent_count

# ----------------------------------------------------

# ------------此部分为memory的相关实现代码 --------------

MEMORY_STORE = MemoryStore(
    MEMORY_DIR,
    MEMORY_INDEX,
    workdir=WORKDIR,
)

MEMORY_MANAGER = MemoryManager(
    client,
    MODEL,
    MEMORY_STORE,
)


# -----------------------------------------------------

# -------------此部分为task系统实现的相关代码-------------


TASKS = TaskStore(TASK_DIR, workdir=WORKDIR)
TASK_BOARD = TaskBoard(TASKS)

# -----------------------------------------------------

# ------------此部分为compact的相关实现代码 --------------

        
COMPACTOR = ContextCompactor(client, MODEL, TRANSCRIPT_DIR, TOOL_RESULTS_DIR)

MAX_REACTIVE_RETRIES = 1

# ------------------------------------------------------

PERMISSIONS = PermissionManager(
    WORKDIR,
    get_mcp_policy=MCP_MANAGER.get_tool_policy,
)


HOOKS = HookRegistry()
DEFAULT_HOOKS = DefaultHooks(WORKDIR)

HOOKS.register(
    "UserPromptSubmit",
    DEFAULT_HOOKS.context_inject_hook,
)
HOOKS.register(
    "PreToolUse",
    DEFAULT_HOOKS.log_hook,
)
HOOKS.register(
    "PreToolUse",
    PERMISSIONS.check_permission,
)
HOOKS.register(
    "PostToolUse",
    DEFAULT_HOOKS.large_output_hook,
)
HOOKS.register(
    "Stop",
    DEFAULT_HOOKS.summary_hook,
)


SUBAGENT_EXECUTOR = SubagentExecutor(
    client,
    MODEL,
    workdir=WORKDIR,
    system_prompt=SUB_SYSTEM_PROMPT,
    tools=SUB_TOOLS,
    files=FILES,
    skill_loader=SKILL_LOADER,
    hooks=HOOKS,
)

SUBAGENTS = SubagentManager(
    TASK_BOARD,
    SUBAGENT_EXECUTOR,
    workdir=WORKDIR,
    max_workers=MAX_SUBAGENTS,
)

SUBAGENT_TOOLS = SubagentTools(SUBAGENTS)

TOOL_DISPATCHER = ToolDispatcher(
    hooks=HOOKS,
    background=BACKGROUND,
)

TOOL_HANDLERS = {
    "bash": SHELL.run_bash,
    "read_file": FILES.run_read_file,
    "write_file": FILES.run_write_file,
    "edit_file": FILES.run_edit_file,
    "glob": FILES.run_glob,
    "load_skill": SKILL_LOADER.load,

    "create_task": TASK_BOARD.run_create_task,
    "update_task": TASK_BOARD.run_update_task,
    "list_tasks": TASK_BOARD.run_list_tasks,
    "get_task": TASK_BOARD.run_get_task,
    "claim_task": TASK_BOARD.run_claim_task,
    "complete_task": TASK_BOARD.run_complete_task,

    "task": SUBAGENT_TOOLS.run_subagent,
    "subagent_status": SUBAGENT_TOOLS.run_subagent_status,
    "subagent_cancel": SUBAGENT_TOOLS.run_subagent_cancel,

    "compact": run_compact,
}


def agent_loop(messages: list[dict],active_request: str) -> str:
    """
    Agent loop for the coding agent.
    messages: 消息列表
    active_request: 当前用户请求
    """
    #rounds_since_todo = 0
    rounds_since_task = 0
    reactive_retries = 0
    relevant = MEMORY_MANAGER.load_memories(messages)
    
    while True:

        inject_async_results(messages) # 将背景任务以及子Agent的结果注入到messages中，大模型会根据这些结果继续推理
        messages[:] = COMPACTOR.prepare(messages, active_request)
        system_prompt = build_system_prompt(
            workdir=WORKDIR,
            max_subagents=MAX_SUBAGENTS,
            skill_catalog=SKILL_LOADER.catalog(),
            memory_index=MEMORY_STORE.read_memory_index(),
            available_mcp_servers=MCP_MANAGER.available_servers(),
            connected_mcp_servers=MCP_MANAGER.connected_servers(),
            relevant_memories=relevant,
        )
        tools, handlers = MCP_MANAGER.assemble_tool_pool(
            builtin_tools=TOOLS,
            builtin_handlers=TOOL_HANDLERS,
        )
        try:
            response = client.messages.create(
                model=MODEL,
                system=system_prompt,
                messages=messages,
                tools=tools,
                max_tokens=8192,
            )
            reactive_retries = 0
        except Exception as error:
            too_long = any(text in str(error).lower()
                           for text in ("prompt_too_long", "too many tokens"))
            if too_long and reactive_retries < MAX_REACTIVE_RETRIES:
                print("[reactive compact]")
                messages[:] = COMPACTOR.reactive_compact(messages, active_request)
                reactive_retries += 1
                continue
            raise

        messages.append({
            "role": "assistant",
            "content": response.content,
        })
        tool_calls = [block for block in response.content if block.type == "tool_use"]
        if not tool_calls: # no tool calls, end of conversation
            # 模型没有请求工具，不代表所有后台工作都结束。
            while True:
                injected = inject_async_results(
                    messages
                )

                if injected:
                    break

                if (
                    not SUBAGENTS.has_running()
                    and not BACKGROUND.has_running()
                ):
                    # 后台任务可能恰好在：
                    # “上次收集之后、状态检查之前”完成。
                    # 所以确认没有运行后，再收集一次。
                    injected = inject_async_results(
                        messages
                    )
                    break

                # 仅程序等待，不反复调用 LLM。
                time.sleep(0.1)

            if injected:
                # 新结果进入 messages 后，
                # 重新调用模型判断下一步。
                continue

            force = HOOKS.trigger("Stop", messages)
            if force:
                messages.append({"role": "user", "content": force})
                continue

            """
            这个函数暂时不用，因为它和subagent的功能汇总结合到一起了。
            still_running = drain_background_tasks(messages, timeout=120.0)
            if still_running:
                print(f"[background] still running: {still_running}")
            """
            if MEMORY_MANAGER.extract_memories(messages):
                MEMORY_MANAGER.consolidate_memories()
            return

        results = []
        #used_todo = False
        used_task = False
        compact_requested = False
        for tool_call in tool_calls:
            print(f"Tool call: {tool_call.name}")
            #if tool_call.name == "todo_write":
            #    used_todo = True
            if tool_call.name == "compact":
                compact_requested = True
            if tool_call.name in ("create_task", "update_task", "claim_task", "complete_task"):
                used_task = True
            #trigger_hooks("PreToolUse", tool_call)
            #tool_result = TOOL_HANDLERS[tool_call.name](**tool_call.input)
            tool_result = TOOL_DISPATCHER.execute_tool(tool_call, handlers)
            suffix = "... [terminal preview truncated]" if len(tool_result) > 500 else ""
            print(f"Tool result: {tool_result[:500]}{suffix}")
            results.append({
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "content": tool_result,
            })
            #trigger_hooks("PostToolUse", tool_call, tool_result)
        rounds_since_task = 0 if used_task else rounds_since_task + 1
        # reminder to sync the task board every 3 rounds
        if rounds_since_task > 3:
            results.append({
                "type": "text",
                "text": (
                    "<reminder>Update the task board. "
                    "Use create_task to plan, claim_task to start, "
                    "and complete_task when finished. "
                    "Call list_tasks if you need the current state.</reminder>"
                ),
            })
            rounds_since_task = 0
        
        messages.append({
            "role": "user",
            "content": results,
        })
        if compact_requested:
            messages[:] = COMPACTOR.compact_history(messages, active_request)




def cleanup_program() -> None:
    """程序退出时统一清理；重复调用不会重复执行。"""
    global _cleanup_started

    if _cleanup_started:
        return

    _cleanup_started = True
    print("\n[shutdown] 正在停止后台运行并清理资源……")

    unfinished = []

    # 1. 通知全部子 Agent 停止，最多等待 5 秒。
    try:
        unfinished = SUBAGENTS.shutdown(
            timeout_seconds=5.0
        )
    except Exception as exc:
        print(
            "[shutdown] 子 Agent 清理出错："
            f"{type(exc).__name__}: {exc}"
        )

    # 2. 清理主 Agent 启动的 Shell 子进程。
    try:
        SHELL.stop_all_shell_processes()
    except Exception as exc:
        print(
            "[shutdown] Shell 清理出错："
            f"{type(exc).__name__}: {exc}"
        )

    # 3. 断开 MCP。
    try:
        MCP_MANAGER.disconnect_all_mcp()
    except Exception as exc:
        print(
            "[shutdown] MCP 清理出错："
            f"{type(exc).__name__}: {exc}"
        )

    # 4. 展示尚未被收集的结果。
    # Shell/MCP 清理期间，子 Agent 也可能刚好完成交接。
    try:
        completed = SUBAGENTS.collect()

        for state in completed:
            print(
                f"\n[shutdown] 子 Agent 结果：{state.run_id}"
            )
            print(format_subagent_result(state))

        for run_id in unfinished:
            snapshot = SUBAGENTS.get(run_id)

            if snapshot["status"] in ("running", "cancelling"):
                print(
                    f"[shutdown] {run_id}："
                    "等待期限已到，当前仍未完成交接；"
                    "不会将其标记为取消完成。"
                )

    except Exception as exc:
        print(
            "[shutdown] 结果展示出错："
            f"{type(exc).__name__}: {exc}"
        )

    print("[shutdown] 退出清理流程结束。")


# 原来的两项注册在前面已经执行。
# 现在统一交给 cleanup_program，避免退出时重复清理。
atexit.register(cleanup_program)


if __name__ == "__main__":
    try:
        print(f"Starting {MODEL} agent at {os.getcwd()}")
        print("Type 'exit' to end the conversation.")
        history = []
        while True:
            try:
                # \001/\002 tell Readline the ANSI escapes have zero display width.
                query = input("\001\033[36m\002s01 >> \001\033[0m\002")
            except (EOFError, KeyboardInterrupt):# Ctrl+C or Ctrl+D exit
                break
            if query.strip().lower() in ("q", "exit", ""):# q or exit or empty input exit
                break
            HOOKS.trigger("UserPromptSubmit", query)
            history.append({"role": "user", "content": query})
            agent_loop(history, query)
            # Print the model's final text response
            response_content = history[-1]["content"]
            if isinstance(response_content, list):# print the model's final text response
                for block in response_content:
                    if getattr(block, "type", None) == "text":# print the model's final text response
                        print(block.text)
            print()
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_program()
