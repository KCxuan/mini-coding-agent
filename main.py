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
import json

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

from CodingAgent.context_budget import (
    ContextBudgetError,
    estimate_request_tokens,
)
from CodingAgent.project_instructions import load_project_instructions

dotenv.load_dotenv()
CONFIG = load_config()

WORKDIR = CONFIG.workdir
PROJECT_INSTRUCTIONS = load_project_instructions(WORKDIR)
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

SUB_SYSTEM_PROMPT = build_subagent_prompt(WORKDIR, SKILL_LOADER.catalog(), project_instructions=PROJECT_INSTRUCTIONS)

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

SHELL = ShellRunner(WORKDIR, output_dir=TOOL_RESULTS_DIR)

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
    subagent_tool_counts: dict[str, int] | None = None,
) -> int:
    shell_count = BACKGROUND.inject_background_results(
        messages
    )
    subagent_count = SUBAGENT_TOOLS.inject_subagent_results(
        messages,
        tool_counts=subagent_tool_counts,
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

        
COMPACTOR = ContextCompactor(
    client, MODEL, TRANSCRIPT_DIR, TOOL_RESULTS_DIR,
    context_window_tokens=CONFIG.context_window_tokens,
)

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
    "grep": FILES.run_grep,
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

# ------------- 主循环的输出异常判断 -------------

# 首次请求的输出额度
MAIN_OUTPUT_TOKENS = 16384

# 截断后重试的输出额度
MAIN_RETRY_OUTPUT_TOKENS = 16384 * 2

# 首次请求之外，最多再请求一次
MAX_RESPONSE_RETRIES = 1


class IncompleteResponseError(RuntimeError):
    """未能取得可继续使用的模型响应。"""


def request_usable_response(
    messages: list[dict],
    system_prompt: str,
    tools: list,
):
    max_tokens = MAIN_OUTPUT_TOKENS

    # MAX_RESPONSE_RETRIES = 1 时：
    # attempt 分别是 0、1，总共最多请求两次。
    for attempt in range(MAX_RESPONSE_RETRIES + 1):
        response = client.messages.create(
            model=MODEL,
            system=system_prompt,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )

        stop_reason = getattr(response, "stop_reason", None)
        blocks = response.content or []

        # 空字符串、只有空格的文本，不算有效正文。
        has_text = any(
            getattr(block, "type", None) == "text"
            and bool(getattr(block, "text", "").strip())
            for block in blocks
        )

        has_tools = any(
            getattr(block, "type", None) == "tool_use"
            for block in blocks
        )

        # 1. 优先检查截断。
        # 即使已经出现正文或工具调用，也不使用这份响应。
        if stop_reason == "max_tokens":
            problem = "模型输出达到上限，回答被截断"
            max_tokens = MAIN_RETRY_OUTPUT_TOKENS

        # 2. 对暂未支持的停止原因，明确报错。
        # 不擅自把未知状态当成正常结束。
        elif stop_reason not in ("end_turn", "tool_use"):
            raise IncompleteResponseError(
                f"暂未处理的停止原因：{stop_reason!r}"
            )

        # 3. 模型说要调用工具，却没有返回工具调用。
        elif stop_reason == "tool_use" and not has_tools:
            problem = "模型声明要调用工具，但没有返回工具调用"

        # 4. 停止原因与工具内容矛盾，不执行工具。
        elif stop_reason == "end_turn" and has_tools:
            raise IncompleteResponseError(
                "模型声明回答结束，却同时返回了工具调用"
            )

        # 5. 响应完整，且有正文或工具调用，交回原流程。
        elif has_text or has_tools:
            return response

        # 6. 没有正文，也没有工具调用：
        # 包括空响应、只有思考内容、只有空白文本。
        else:
            problem = "模型没有返回正文或工具调用"

        # 已经用完重试机会。
        if attempt == MAX_RESPONSE_RETRIES:
            raise IncompleteResponseError(
                f"{problem}；已重试 {MAX_RESPONSE_RETRIES} 次，"
                "本轮未完成"
            )

        print(
            f"[response retry] {problem}；"
            f"准备重试，输出上限为 {max_tokens}"
        )

# --------------------------------------------------------

# 首次允许 60 轮，每次续跑也增加 60 轮。
AGENT_ROUND_BATCH = 60


class AgentRoundLimitError(RuntimeError):
    """轮数额度已用完，用户未授权继续。"""


def confirm_more_rounds(rounds_used: int) -> bool:
    """询问用户是否为当前任务增加一批执行轮数。"""

    while True:
        try:
            answer = input(
                f"\n当前任务已运行 {rounds_used} 轮，尚需继续处理。\n"
                f"是否再允许 {AGENT_ROUND_BATCH} 轮？[y/N]: "
            ).strip().lower()

        except EOFError:
            # 输入通道关闭时，不自动授权继续。
            return False

        if answer in ("y", "yes"):
            return True

        if answer in ("", "n", "no"):
            return False

        print("请输入 y 继续，或输入 n / 直接回车停止。")

def agent_loop(messages: list[dict],active_request: str) -> str:
    """
    Agent loop for the coding agent.
    messages: 消息列表
    active_request: 当前用户请求
    """
    #rounds_since_todo = 0
    rounds_since_task = 0
    reactive_retries = 0

    # 记录总共已使用多少轮。
    rounds_used = 0
    # 用户目前单次授权的总轮数
    rounds_allowed = AGENT_ROUND_BATCH
    # 用户本次循环的工具调用次数
    tool_call_count = 0

    # 只保存本轮启动的子Agent 键为run_id
    subagent_tool_counts: dict[str, int] = {}

    relevant = MEMORY_MANAGER.load_memories(messages)
    
    while True:

        if rounds_used >= rounds_allowed:
            if not confirm_more_rounds(rounds_used):
                raise AgentRoundLimitError(
                    f"已运行 {rounds_used} 轮，"
                    "用户未授权继续，本次任务已停止。"
                )
            rounds_allowed += AGENT_ROUND_BATCH
            print(
                f"[继续] 总额度已增加到 {rounds_allowed} 轮，"
                f"即将开始第 {rounds_used + 1} 轮。"
            )
        rounds_used += 1

        inject_async_results(messages, subagent_tool_counts) # 将背景任务以及子Agent的结果注入到messages中，大模型会根据这些结果继续推理
        # messages[:] = COMPACTOR.prepare(messages, active_request)
        system_prompt = build_system_prompt(
            workdir=WORKDIR,
            max_subagents=MAX_SUBAGENTS,
            skill_catalog=SKILL_LOADER.catalog(),
            memory_index=MEMORY_STORE.read_memory_index(),
            available_mcp_servers=MCP_MANAGER.available_servers(),
            connected_mcp_servers=MCP_MANAGER.connected_servers(),
            relevant_memories=relevant,
            project_instructions=PROJECT_INSTRUCTIONS,
        )
        tools, handlers = MCP_MANAGER.assemble_tool_pool(
            builtin_tools=TOOLS,
            builtin_handlers=TOOL_HANDLERS,
        )

        # 预留较大的输出额度，让截断后的重试也有空间。
        output_reserve = max(
            MAIN_OUTPUT_TOKENS,
            MAIN_RETRY_OUTPUT_TOKENS,
        )

        messages[:] = COMPACTOR.prepare(
            messages,
            active_request,
            system_prompt=system_prompt,
            tools=tools,
            output_reserve=output_reserve,
        )

        estimated = estimate_request_tokens(
            system_prompt,
            messages,
            tools,
        )

        print(
            f"[context] 输入约 {estimated:,} token；"
            f"含输出预留约占 "
            f"{(estimated + output_reserve) / CONFIG.context_window_tokens:.1%}"
        )

        try:
            response = request_usable_response(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
            )

            usage = getattr(response, "usage", None)
            if usage is not None:
                print(f"[usage 原始统计] {usage}")

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
                    messages,
                    subagent_tool_counts,
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
                        messages,
                        subagent_tool_counts,
                    )
                    break

                # 仅程序等待，不反复调用 LLM。
                time.sleep(0.1)

            if injected:
                # 新结果进入 messages 后，
                # 重新调用模型判断下一步。
                continue

            force = HOOKS.trigger("Stop", messages, tool_call_count)
            if force:
                messages.append({"role": "user", "content": force})
                continue

            """
            这个函数暂时不用，因为它和subagent的功能汇总结合到一起了。
            still_running = drain_background_tasks(messages, timeout=120.0)
            if still_running:
                print(f"[background] still running: {still_running}")
            """

            subagent_total = sum(subagent_tool_counts.values())

            print(
                f"[tools] 主 Agent: {tool_call_count}；"
                f"子 Agent: {subagent_total}；"
                f"合计: {tool_call_count + subagent_total}"
            )
            
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

            tool_call_count += 1
            tool_result = TOOL_DISPATCHER.execute_tool(tool_call, handlers)
            
            if tool_call.name == "task":
                try:
                    receipt = json.loads(tool_result)
                except (ValueError, TypeError):
                    receipt = {}

                if isinstance(receipt, dict):
                    run_id = receipt.get("run_id")

                    if (
                        receipt.get("status") == "started"
                        and isinstance(run_id, str)
                        and run_id
                    ):
                        subagent_tool_counts.setdefault(run_id, 0)

            
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
    except (IncompleteResponseError, AgentRoundLimitError, ContextBudgetError) as error:
        print(f"\n[未完成] {error}")
        print(
            "当前程序将退出并执行清理。"
            "此前已经执行的文件修改不会自动撤销。"
        )
        raise SystemExit(1)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_program()
