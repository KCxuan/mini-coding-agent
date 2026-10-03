import base64
import binascii
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4


# 普通文字，或者模型可以接收的内容块列表。
ToolContent = str | list[dict]

MEDIA_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

MAX_IMAGE_BYTES = 32 * 1024 * 1024

IMAGE_REMOVED_TEXT = (
    "[Image data removed from history. "
    "Use read_image with the saved file path to inspect it again.]"
)


def detect_media_type(raw: bytes) -> str:
    """根据文件头识别格式，避免只依赖文件扩展名。"""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Unsupported image format: expected PNG, JPEG, GIF or WebP")


def check_image_bytes(raw: bytes) -> str:
    if not raw:
        raise ValueError("Image file is empty")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 32 MiB limit")
    return detect_media_type(raw)


def save_image(data: str, media_type: str, image_dir: Path) -> Path:
    """将 MCP 返回的 Base64 图片保存为二进制原图。"""
    if media_type not in MEDIA_EXTENSIONS:
        raise ValueError(f"Unsupported image MIME type: {media_type!r}")
    if not isinstance(data, str):
        raise ValueError("Image data must be a Base64 string")

    # 在解码前先限制大小，避免读取过大的图片数据。
    max_encoded_chars = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
    if len(data) > max_encoded_chars:
        raise ValueError("Encoded image exceeds the size limit")

    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Invalid Base64 image data") from error

    actual_type = check_image_bytes(raw)
    if actual_type != media_type:
        raise ValueError(
            f"Image MIME mismatch: declared {media_type}, detected {actual_type}"
        )

    directory = Path(image_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid4().hex}{MEDIA_EXTENSIONS[actual_type]}"

    with path.open("xb") as saved:
        saved.write(raw)

    return path


def make_image_content(
    path: Path,
    data: str,
    media_type: str,
) -> list[dict]:
    """路径保存在文字中；图片块只包含接口支持的字段。"""
    return [
        {
            "type": "text",
            "text": (
                f"Image file: {Path(path).resolve()}\n"
                f"Media type: {media_type}\n"
                "Use read_image(path) to reload this image after compaction."
            ),
        },
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": data,
            },
        },
    ]


def read_image(path: Path) -> list[dict]:
    """读取原图，构造与 MCP 图片相同的返回内容。"""
    resolved = Path(path).resolve()

    with resolved.open("rb") as image:
        raw = image.read(MAX_IMAGE_BYTES + 1)

    media_type = check_image_bytes(raw)
    data = base64.b64encode(raw).decode("ascii")
    return make_image_content(resolved, data, media_type)


def normalize_tool_result(value: Any) -> ToolContent:
    """识别图片结果；其他返回值沿用原来的字符串转换。"""
    if isinstance(value, str):
        return value

    if isinstance(value, list) and value and all(
        isinstance(block, dict)
        and block.get("type") in {"text", "image"}
        for block in value
    ):
        return value

    return str(value)


def has_images(content: Any) -> bool:
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "image"
        for block in content
    )


def tool_result_text(content: Any) -> str:
    """只取工具结果的文字，不读取图片中的 Base64。"""
    normalized = normalize_tool_result(content)
    if isinstance(normalized, str):
        return normalized

    return "\n".join(
        str(block.get("text") or "")
        for block in normalized
        if block.get("type") == "text"
    )


def tool_result_preview(content: Any, limit: int = 500) -> str:
    """终端优先展示图片路径，再展示工具文字。"""
    normalized = normalize_tool_result(content)
    text = tool_result_text(normalized)

    if has_images(normalized):
        texts = [
            str(block.get("text") or "")
            for block in normalized
            if block.get("type") == "text"
        ]
        metadata = [item for item in texts if item.startswith("Image file: ")]
        other_text = [item for item in texts if not item.startswith("Image file: ")]
        count = sum(block.get("type") == "image" for block in normalized)

        text = "\n".join([
            f"[{count} image(s); image data omitted from terminal]",
            *metadata,
            *other_text,
        ])

    suffix = "... [terminal preview truncated]" if len(text) > limit else ""
    return text[:limit] + suffix


def without_image_data(content: Any) -> ToolContent:
    """移除图片内容，保留路径、文字和内容块顺序。"""
    normalized = normalize_tool_result(content)
    if isinstance(normalized, str):
        return normalized

    return [
        {"type": "text", "text": IMAGE_REMOVED_TEXT}
        if block.get("type") == "image"
        else deepcopy(block)
        for block in normalized
    ]


def block_dict(block: Any) -> dict:
    """兼容字典和 Anthropic SDK 返回的内容块。"""
    if isinstance(block, dict):
        return block
    return block.model_dump(mode="json")


def text_history(messages: list[dict]) -> list[dict]:
    """生成供摘要和转录使用的副本，不改变实际请求历史。"""
    history = deepcopy(messages)

    for message in history:
        content = message.get("content")
        if not isinstance(content, list):
            continue

        cleaned = []
        for block in content:
            item = block_dict(block)

            if item.get("type") == "image":
                item = {"type": "text", "text": IMAGE_REMOVED_TEXT}
            elif item.get("type") == "tool_result":
                item["content"] = without_image_data(item.get("content", ""))

            cleaned.append(item)

        message["content"] = cleaned

    return history


def image_count(messages: list[dict]) -> int:
    """统计用户图片，以及 tool_result 内的图片。"""
    count = 0

    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue

        for block in content:
            item = block_dict(block)

            if item.get("type") == "image":
                count += 1
            elif item.get("type") == "tool_result":
                nested = item.get("content")
                if isinstance(nested, list):
                    count += sum(
                        block_dict(part).get("type") == "image"
                        for part in nested
                    )

    return count