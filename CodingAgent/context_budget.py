import json


CONTEXT_WINDOW_TOKENS = 1_000_000
COMPACT_TRIGGER_RATIO = 0.90


class ContextBudgetError(RuntimeError):
    """上下文无法安全放入请求预算。"""
    pass


def json_default(value):
    # 支持 Anthropic SDK 返回的内容块。
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    raise TypeError(f"无法序列化：{type(value).__name__}")


def dump_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        default=json_default,
        separators=(",", ":"),
    )


def estimate_request_tokens(system_prompt, messages, tools):
    """粗估完整文本请求，包括消息中的思考和工具内容。"""
    request_text = dump_json({
        "system": system_prompt,
        "messages": messages,
        "tools": tools,
    })

    # 使用整数计算：这里 10 个单位代表约 1 token。
    units = 0

    for char in request_text:
        if "\u4e00" <= char <= "\u9fff":
            units += 6
        elif "a" <= char <= "z" or "A" <= char <= "Z":
            units += 3
        else:
            units += 10

    # 向上取整。
    return (units + 9) // 10