from pathlib import Path
import re

import yaml


class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills: dict[str, dict[str, str]] = {}
        self.skills_dir = skills_dir
        self.scan()

    @staticmethod
    def parse_manifest(content: str) -> tuple[dict, str]:
        # 解析SKILL.md文件中的元数据和内容
        text = content.replace("\r\n", "\n")
        stripped = text.lstrip()
        if not stripped.startswith("---"):
            return {}, text
        match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", stripped, flags=re.DOTALL)
        if not match:
            return {}, text
        raw_yaml = match.group(1)
        body = stripped[match.end():]
        try:
            metadata = yaml.safe_load(raw_yaml)
        except yaml.YAMLError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return metadata, body.strip()
    
    def scan(self):
        self.skills.clear()
        skill_root = self.skills_dir.resolve()
        for manifest in sorted(self.skills_dir.glob("*/SKILL.md")):
            if (not manifest.is_file() or not manifest.resolve().is_relative_to(skill_root)):
                continue
            content = manifest.read_text(encoding="utf-8")
            metadata, body = self.parse_manifest(content)
            raw_name = metadata.get("name", "")
            name = raw_name.strip() if isinstance(raw_name, str) else ""
            name = name or manifest.parent.name
            raw_description = metadata.get("description")
            description = (raw_description.strip()
                           if isinstance(raw_description, str) else "")
            description = description or body.split("\n", 1)[0]
            description = " ".join(str(description).lstrip("# ").split())
            self.skills[name] = ({
                "name": name,
                "description": description,
                "content": content,
            })
    
    def catalog(self) -> str:
        if not self.skills:
            return "No skills found."
        return "\n".join(
            f"{skill['name']}: {skill['description']}"
            for skill in self.skills.values()
        )


    def load(self, name: str) -> str:
        skill = self.skills.get(name)
        if skill:
            return skill["content"]
        available = ", ".join(sorted(self.skills.keys()))
        return f"Skill {name} not found. Available skills: {available}"