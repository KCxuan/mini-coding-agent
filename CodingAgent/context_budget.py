import json

from .images import image_count, text_history


COMPACT_TRIGGER_RATIO = 0.90

# 这些值针对当前 DeepSeek Flash 接口。
IMAGE_TOKEN_BUDGET = 1024
MAX_REQUEST_BODY_BYTES = 48 * 1024 * 1024
MAX_IMAGES_PER_REQUEST = 600


class ContextBudgetError(RuntimeError):
    """上下文无法安全放入请求预算。"""
    pass


def json_default(value):
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


def request_payload(system_prompt, messages, tools):
    return {
        "system": system_prompt,
        "messages": messages,
        "tools": tools,
    }


def estimate_request_tokens(system_prompt, messages, tools):
    """文字沿用原估算；图片按单张上限单独预留。"""
    request_text = dump_json(request_payload(
        system_prompt,
        text_history(messages),
        tools,
    ))

    units = 0
    for char in request_text:
        if "\u4e00" <= char <= "\u9fff":
            units += 6
        elif "a" <= char <= "z" or "A" <= char <= "Z":
            units += 3
        else:
            units += 10

    text_tokens = (units + 9) // 10
    return text_tokens + image_count(messages) * IMAGE_TOKEN_BUDGET


def estimate_request_bytes(system_prompt, messages, tools):
    """请求体大小包含实际 Base64，不能使用文字摘要副本。"""
    payload = request_payload(system_prompt, messages, tools)
    return len(dump_json(payload).encode("utf-8"))