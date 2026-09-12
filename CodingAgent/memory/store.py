import re
from pathlib import Path

import yaml


MEMORY_TYPES = ("user", "feedback", "project", "reference")

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析frontmatter，返回元数据和内容
    text: 文本
    return: 元数据和内容
    """
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(metadata, dict):
        return {}, text
    return metadata, parts[2].lstrip()

def memory_slug(name: str) -> str:
    """生成对应记忆的文件名slug
    name: 记忆名称
    return: 文件名slug
    """
    slug = re.sub(r"[^\w]+", "-", name.lower()).strip("-_")
    return slug or "memory"

def memory_document(name: str, mem_type: str, description: str, body: str) -> str:
    """生成记忆文档
    name: 记忆名称
    mem_type: 记忆类型
    description: 记忆描述
    body: 记忆内容
    return: 记忆文档
    """
    metadata = yaml.safe_dump(
        {"name": name, "description": description, "type": mem_type},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{metadata}\n---\n\n{body.strip()}\n"


class MemoryStore:
    def __init__(
        self,
        memory_dir: Path,
        index_path: Path,
        *,
        workdir: Path,
    ):
        self.directory = memory_dir
        self.index_path = index_path
        self.workdir = workdir.resolve()

    def memory_path(self, filename: str, allow_index: bool = False) -> Path:
        """实现路径方面的约束，防止路径穿越和文件名冲突
        filename: 文件名
        allow_index: 是否允许使用index文件
        return: 文件路径
        """
        if Path(filename).name != filename:
            raise ValueError(f"Invalid filename: {filename}")
        if filename == self.index_path.name and not allow_index:
            raise ValueError("The memory index is not a memory record")
        
        root = self.directory.resolve()
        if not root.is_relative_to(self.workdir):
            raise ValueError("Memory directory escapes the workspace")
        path = (root / filename).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Memory path escapes the store: {filename}")
        return path

    def write_memory_file(self, name: str, mem_type: str, description: str, body: str) -> Path:
        """写入记忆文件
        name: 记忆名称
        mem_type: 记忆类型
        description: 记忆描述
        body: 记忆内容
        return: 记忆文件路径
        """
        if not name.strip():
            raise ValueError("Memory name cannot be empty")
        if mem_type not in MEMORY_TYPES:
            raise ValueError(f"Unknown memory type: {mem_type}")
        if not description.strip() or not body.strip():
            raise ValueError("Memory description and body cannot be empty")
        
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.memory_path(f"{memory_slug(name)}.md")
        path.write_text(
            memory_document(name, mem_type, description, body), encoding="utf-8"
        )
        self.rebuild_memory_index()
        return path

    def rebuild_memory_index(self):
        """
        重建记忆索引
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        lines = []
        for path in self.directory.glob("*.md"):
            if path.name == self.index_path.name:
                continue
            try:
                path = self.memory_path(path.name)
            except ValueError:
                continue
            
            metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            name = " ".join(str(metadata.get("name") or path.stem).split())
            first_line = next((line for line in body.splitlines() if line.strip()), "")
            description = " ".join(
                str(metadata.get("description") or first_line).split()
            )
            lines.append(f"- [{name}]({path.name}) - {description}")
        self.memory_path(self.index_path.name, allow_index=True).write_text(
            "\n".join(lines) + "\n" if lines else "", 
            encoding="utf-8"
        )

    def read_memory_index(self) -> str:
        """读取记忆索引"""
        try:
            return self.memory_path(self.index_path.name, allow_index=True).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""


    def read_memory_file(self, filename: str) -> str:
        """读取记忆文件"""
        try:
            return self.memory_path(filename).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""


    def list_memory_files(self) -> list[dict]:
        """列出记忆文件"""
        records = []
        if not self.directory.exists():
            return records
        for path in self.directory.glob("*.md"):
            if path.name == self.index_path.name:
                continue
            try:
                path = self.memory_path(path.name)
            except ValueError:
                continue
            metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            records.append({
                "name": metadata.get("name", path.stem),
                "type": metadata.get("type", "unknown"),
                "description": metadata.get("description", ""),
                "filename": path.name,
                "body": body.strip(),
            })
        return records

    def replace_records(
        self,
        records: list[dict],
        consolidated: list[dict],
    ) -> None:
        """保存旧文件快照，替换文件；写入失败时恢复旧文件"""
        snapshot = {
            record["filename"]: self.memory_path(record["filename"]).read_text(
                encoding="utf-8"
            )
            for record in records
        }
        try:
            for path in self.directory.glob("*.md"):
                if path.name != self.index_path.name:
                    try:
                        # unlink是删除文件的意思
                        self.memory_path(path.name).unlink()
                    except ValueError:
                        continue
            for record in consolidated:
                path = self.memory_path(f"{memory_slug(record['name'])}.md")
                path.write_text(
                    memory_document(
                        record["name"],
                        record["type"],
                        record["description"],
                        record["body"],
                    ),
                    encoding="utf-8",
                )
            self.rebuild_memory_index()
        except Exception:
            # 如果中途失败了，就重新来一次
            for path in self.directory.glob("*.md"):
                if path.name != self.index_path.name:
                    try:
                        self.memory_path(path.name).unlink()
                    except ValueError:
                        continue
            for filename, content in snapshot.items():
                self.memory_path(filename).write_text(content, encoding="utf-8")
            self.rebuild_memory_index()
            raise
