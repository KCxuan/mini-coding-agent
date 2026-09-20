from pathlib import Path


def load_project_instructions(workdir: Path) -> str:
    """启动时读取根目录 AGENT.md；缺失或空白时返回空字符串。"""
    path = workdir / "AGENT.md"

    try:
        content = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError) as error:
        raise RuntimeError(
            f"无法读取项目说明文件 {path}: {error}"
        ) from error

    return content if content.strip() else ""