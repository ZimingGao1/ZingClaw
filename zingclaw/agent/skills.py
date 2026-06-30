"""Skills loader for agent capabilities."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"

_STRIP_SKILL_FRONTMATTER = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?",
    re.DOTALL,
)


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """

    def __init__(
        self,
        workspace: Path,
        builtin_skills_dir: Path | None = None,
        disabled_skills: set[str] | None = None,
        *,
        skills_dir: Path | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace_skills = self.workspace / "skills"
        self.skills_dir = (
            Path(skills_dir).expanduser().resolve()
            if skills_dir is not None
            else self.workspace_skills
        )
        self.builtin_skills = (
            Path(builtin_skills_dir).expanduser().resolve()
            if builtin_skills_dir is not None
            else BUILTIN_SKILLS_DIR
        )
        self.disabled_skills = {
            str(name).strip().casefold()
            for name in (disabled_skills or set())
            if str(name).strip()
        }

    def discover(self, *, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """Compatibility alias for skill discovery."""
        return self.list_skills(filter_unavailable=filter_unavailable)

    def _skill_entries_from_dir(
        self,
        base: Path,
        source: str,
        *,
        skip_names: set[str] | None = None,
    ) -> list[dict[str, str]]:
        if not base.exists():
            return []

        entries: list[dict[str, str]] = []
        hidden = {name.casefold() for name in (skip_names or set())}

        for skill_dir in sorted(base.iterdir(), key=lambda item: item.name.lower()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            name = skill_dir.name
            if name.casefold() in hidden or name.casefold() in self.disabled_skills:
                continue
            entries.append({"name": name, "path": str(skill_file), "source": source})
        return entries

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.

        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        skills = self._skill_entries_from_dir(self.workspace_skills, "workspace")
        workspace_names = {entry["name"] for entry in skills}

        if self.skills_dir != self.workspace_skills and self.skills_dir.exists():
            configured_entries = self._skill_entries_from_dir(
                self.skills_dir,
                "configured",
                skip_names=workspace_names,
            )
            skills.extend(configured_entries)
            workspace_names |= {entry["name"] for entry in configured_entries}

        if self.builtin_skills.exists():
            skills.extend(
                self._skill_entries_from_dir(
                    self.builtin_skills,
                    "builtin",
                    skip_names=workspace_names,
                )
            )

        if filter_unavailable:
            return [
                skill
                for skill in skills
                if self._check_requirements(self._get_skill_meta(skill["name"]))
            ]
        return skills

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        normalized = name.casefold()
        roots = [self.workspace_skills]
        if self.skills_dir != self.workspace_skills:
            roots.append(self.skills_dir)
        roots.append(self.builtin_skills)

        for root in roots:
            if not root.exists():
                continue
            for skill_dir in root.iterdir():
                if not skill_dir.is_dir() or skill_dir.name.casefold() != normalized:
                    continue
                path = skill_dir / "SKILL.md"
                if path.exists():
                    return path.read_text(encoding="utf-8")
        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.

        Returns:
            Formatted skills content.
        """
        parts = [
            f"### Skill: {name}\n\n{self._strip_frontmatter(markdown)}"
            for name in skill_names
            if (markdown := self.load_skill(name))
        ]
        return "\n\n---\n\n".join(parts)

    def build_summary(self) -> str:
        """Compatibility alias used by earlier ZingClaw code."""
        return self.build_skills_summary()

    def build_skills_summary(self, exclude: set[str] | None = None) -> str:
        """
        Build a summary of all skills (name, description, availability).

        Full skill content for always-on skills is injected by the framework.
        Other skills are summarized here so the model can reason about their
        existence without being nudged to open SKILL.md paths directly.

        Args:
            exclude: Set of skill names to omit from the summary.

        Returns:
            Markdown-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        hidden = {name.casefold() for name in (exclude or set())}
        lines: list[str] = []

        for entry in all_skills:
            skill_name = entry["name"]
            if skill_name.casefold() in hidden:
                continue
            meta = self._get_skill_meta(skill_name)
            available = self._check_requirements(meta)
            desc = self._get_skill_description(skill_name)
            if available:
                lines.append(f"- **{skill_name}** - {desc}")
            else:
                missing = self._get_missing_requirements(meta)
                suffix = f" (unavailable: {missing})" if missing else " (unavailable)"
                lines.append(f"- **{skill_name}** - {desc}{suffix}")
        return "\n".join(lines)

    def _get_missing_requirements(self, skill_meta: dict[str, Any]) -> str:
        """Get a description of missing requirements."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return ", ".join(
            [f"CLI: {command_name}" for command_name in required_bins if not shutil.which(command_name)]
            + [f"ENV: {env_name}" for env_name in required_env_vars if not os.environ.get(env_name)]
        )

    def get_skill_availability(self, name: str) -> tuple[bool, str]:
        """Return whether a skill can run and why not when it cannot."""
        meta = self._get_skill_meta(name)
        available = self._check_requirements(meta)
        return available, "" if available else self._get_missing_requirements(meta)

    def get_skill_requirements(self, name: str) -> dict[str, list[str]]:
        """Return explicit command/env requirements and currently missing entries."""
        requires = self._get_skill_meta(name).get("requires", {})
        bins = [str(value) for value in requires.get("bins", [])]
        env = [str(value) for value in requires.get("env", [])]
        return {
            "bins": bins,
            "env": env,
            "missing_bins": [value for value in bins if not shutil.which(value)],
            "missing_env": [value for value in env if not os.environ.get(value)],
        }

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self._get_skill_meta(name)
        if meta and meta.get("description"):
            return str(meta["description"])

        content = self.load_skill(name)
        if content:
            summary = self._extract_summary(content)
            if summary:
                return summary
        return name

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return content.strip()
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if match:
            return content[match.end() :].strip()
        return content.strip()

    @staticmethod
    def _parse_nanobot_metadata(raw: object) -> dict[str, Any]:
        """Extract nanobot/openclaw-compatible metadata from a frontmatter field."""
        if isinstance(raw, dict):
            data = raw
        elif isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}
        else:
            return {}

        if not isinstance(data, dict):
            return {}

        for key in ("zingclaw", "nanobot", "openclaw"):
            payload = data.get(key)
            if isinstance(payload, dict):
                return dict(payload)
        return dict(data)

    def _check_requirements(self, skill_meta: dict[str, Any]) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return all(shutil.which(cmd) for cmd in required_bins) and all(
            os.environ.get(var) for var in required_env_vars
        )

    def _get_skill_meta(self, name: str) -> dict[str, Any]:
        """Get normalized metadata for a skill."""
        raw_meta = self.get_skill_metadata(name) or {}
        parsed = self._parse_nanobot_metadata(raw_meta.get("metadata"))
        for key in ("always", "requires", "emoji", "install", "description"):
            if key in raw_meta and key not in parsed:
                parsed[key] = raw_meta[key]
        return parsed

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        return [
            entry["name"]
            for entry in self.list_skills(filter_unavailable=True)
            if (meta := self._get_skill_meta(entry["name"])) and meta.get("always")
        ]

    def get_skill_metadata(self, name: str) -> dict[str, Any] | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        content = self.load_skill(name)
        if not content or not content.startswith("---"):
            return None

        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if not match:
            return None

        try:
            parsed = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None

        if not isinstance(parsed, dict):
            return None

        metadata: dict[str, Any] = {}
        for key, value in parsed.items():
            metadata[str(key)] = value
        return metadata

    @classmethod
    def _extract_summary(cls, content: str) -> str:
        """Extract the first meaningful paragraph from a skill markdown body."""
        body = cls._strip_frontmatter(content)
        if not body:
            return ""

        lines = [line.rstrip() for line in body.splitlines()]
        paragraph: list[str] = []
        in_code_block = False

        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            if not stripped:
                if paragraph:
                    break
                continue
            if stripped.startswith("#"):
                continue
            paragraph.append(stripped)

        if paragraph:
            return " ".join(paragraph)

        for raw_line in lines:
            stripped = raw_line.strip().lstrip("#").strip()
            if stripped:
                return stripped
        return ""
