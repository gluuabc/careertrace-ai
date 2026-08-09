from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.database.database import PROJECT_ROOT

ALLOWED_SKILL_SUFFIXES = {".md", ".txt", ".yaml", ".yml", ".json"}
MAX_SKILL_FILE_BYTES = 128 * 1024


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    directory: Path
    body: str


class SkillRegistry:
    def __init__(self, root: Path | None = None):
        self.root = (root or PROJECT_ROOT / "app" / "skills").resolve()
        self._skills = self._scan()

    def _scan(self) -> dict[str, SkillMetadata]:
        skills: dict[str, SkillMetadata] = {}
        if not self.root.exists():
            return skills
        for path in sorted(self.root.glob("*/SKILL.md")):
            text = self._read_limited(path)
            metadata, body = self._parse_frontmatter(text, path)
            name = str(metadata.get("name") or "").strip()
            description = str(metadata.get("description") or "").strip()
            if not name or not description:
                raise ValueError(f"Skill frontmatter requires name and description: {path}")
            if name in skills:
                raise ValueError(f"Duplicate Skill name: {name}")
            skills[name] = SkillMetadata(name, description, path.parent.resolve(), body)
        return skills

    @staticmethod
    def _parse_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
        if not text.startswith("---\n"):
            raise ValueError(f"Skill is missing YAML frontmatter: {path}")
        end = text.find("\n---\n", 4)
        if end < 0:
            raise ValueError(f"Skill frontmatter is not closed: {path}")
        metadata = yaml.safe_load(text[4:end]) or {}
        if not isinstance(metadata, dict):
            raise ValueError(f"Skill frontmatter must be a mapping: {path}")
        return metadata, text[end + 5 :].strip()

    @staticmethod
    def _read_limited(path: Path) -> str:
        if path.stat().st_size > MAX_SKILL_FILE_BYTES:
            raise ValueError(f"Skill file exceeds {MAX_SKILL_FILE_BYTES} bytes: {path.name}")
        return path.read_text(encoding="utf-8")

    def catalog(self) -> str:
        return "\n".join(
            f"- {item.name}: {item.description}" for item in self._skills.values()
        )

    def names(self) -> tuple[str, ...]:
        return tuple(self._skills)

    def read_skill(self, name: str) -> str:
        try:
            return self._skills[name].body
        except KeyError as error:
            raise ValueError(f"Unknown Skill: {name}") from error

    def read_skill_file(self, name: str, relative_path: str) -> str:
        skill = self._skills.get(name)
        if skill is None:
            raise ValueError(f"Unknown Skill: {name}")
        requested = Path(relative_path)
        if requested.is_absolute() or ".." in requested.parts:
            raise ValueError("Skill file path must remain inside the registered Skill.")
        if requested.suffix.lower() not in ALLOWED_SKILL_SUFFIXES:
            raise ValueError("Unsupported Skill file type.")
        candidate = skill.directory / requested
        current = skill.directory
        for part in requested.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Skill file path contains a symbolic link.")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(skill.directory):
            raise ValueError("Skill file path escapes the registered Skill.")
        if not resolved.is_file():
            raise ValueError("Skill file was not found.")
        return self._read_limited(resolved)


skill_registry = SkillRegistry()
