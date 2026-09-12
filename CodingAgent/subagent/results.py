import json

from .state import SubagentState

def finalize_cancelled_subagent(
    state: SubagentState,
) -> SubagentState:
    """取消时只整理已有记录，不再请求模型或执行工具。"""
    if state.status == "cancelled":
        return state

    previous_summary = state.summary
    previous_remaining = state.remaining

    state.status = "cancelled"
    state.summary = (
        f"本次运行已取消；"
        f"已请求工作模型 {state.turns_used} 轮，"
        f"记录工具结果 {len(subagent_evidence(state))} 条。"
    )

    if previous_summary:
        state.summary += f"\n取消前已有摘要：{previous_summary}"

    state.remaining = (
        "本次运行因取消而结束，不能据此认定原任务完成。"
        "请主 Agent 根据已有证据决定后续处理。"
    )

    if previous_remaining:
        state.remaining += (
            f"\n取消前记录的未完成事项：{previous_remaining}"
        )

    # messages、response_log 和 error 都保留。
    return state


def subagent_evidence(
    state: SubagentState,
) -> list[dict]:
    """从 messages 提取已有返回结果的请求，包含拒绝和报错。"""
    calls = {}
    evidence = []

    for message in state.messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue

        for block in content:
            if block.get("type") == "tool_use":
                calls[block["id"]] = block

            elif block.get("type") == "tool_result":
                call = calls.get(
                    block["tool_use_id"],
                    {},
                )

                item = {
                    "tool_use_id": block["tool_use_id"],
                    "tool": call.get("name", "unknown"),
                    "arguments": call.get("input", {}),
                    "output": block.get("content", ""),
                }

                # 子 Agent 的 bash 包装函数返回 JSON，
                # 其中的退出码来自实际进程。
                if item["tool"] == "bash":
                    try:
                        result = json.loads(item["output"])

                        if (
                            isinstance(result, dict)
                            and "exit_code" in result
                        ):
                            item["exit_code"] = result["exit_code"]

                    except (ValueError, TypeError):
                        pass

                evidence.append(item)

    return evidence

def record_subagent_response(state, response, *, phase: str, max_tokens: int) -> None:
    """记录接口返回的事实，不记录或打印推理内容。"""
    usage = getattr(response, "usage", None)
    record = {
        "phase": phase,
        "turn": state.turns_used,
        "stop_reason": getattr(response, "stop_reason", None),
        "content_types": [
            getattr(block, "type", None) for block in response.content
        ],
        "max_tokens": max_tokens,
        "output_tokens": getattr(usage, "output_tokens", None),
    }
    state.response_log.append(record)
    print(f"[{state.run_id}] response: {json.dumps(record, ensure_ascii=False)}")


def apply_subagent_summary(
    state: SubagentState,
    text: str,
) -> None:
    """解析格式正确的摘要，格式不符合要求时保留原文，不因为解析失败丢掉回答。"""
    if not text.strip():
        state.warnings.append("收尾模型没有返回文本，保留程序生成的摘要和未完成说明。")
        return

    try:
        data = json.loads(text)

        if not isinstance(data, dict):
            raise ValueError("summary must be an object")

        if not isinstance(data.get("summary"), str):
            raise ValueError("missing summary")

        if not isinstance(data.get("remaining"), str):
            raise ValueError("missing remaining")

        state.summary = (
            data["summary"].strip() or state.summary
        )
        state.remaining = data["remaining"].strip()

    except (ValueError, TypeError):
        state.summary = text.strip() or state.summary
        state.remaining = (
            "模型未单独列出剩余问题，"
            "请主 Agent 根据摘要和证据验收。"
        )

def format_subagent_result(
    state: SubagentState,
) -> str:
    """将已完成的 state 整理为主 Agent 可以读取的 JSON。"""

    def preview(value, limit=1200):
        text = (
            value
            if isinstance(value, str)
            else json.dumps(
                value,
                ensure_ascii=False,
            )
        )

        return {
            "text": text[:limit],
            "truncated": len(text) > limit,
        }

    evidence = []

    for item in subagent_evidence(state):
        row = {
            "tool_use_id": item["tool_use_id"],
            "tool": item["tool"],
            "arguments": preview(
                item["arguments"]
            ),
            "output": preview(
                item["output"]
            ),
        }

        if "exit_code" in item:
            row["exit_code"] = item["exit_code"]

        evidence.append(row)

    return json.dumps(
        {
            "run_id": state.run_id,
            "task_id": state.task_id,
            "status": state.status,
            "summary": state.summary,
            "remaining": state.remaining,
            "error": state.error,
            "warnings": state.warnings,
            "turns_used": state.turns_used,
            "response_log": state.response_log,
            "evidence": evidence,
        },
        ensure_ascii=False,
        indent=2,
    )