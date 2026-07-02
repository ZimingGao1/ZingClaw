"""Skills loader for agent capabilities."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"

_STRIP_SKILL_FRONTMATTER = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class SkillSpec:
    """Resolved information about one discoverable skill."""

    name: str
    path: Path
    source: str
    description: str
    available: bool
    missing_requirements: str = ""
    always: bool = False


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
        enabled_skills: set[str] | None = None,
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
        self.disabled_skills = self._normalize_skill_names(disabled_skills or set())
        self.enabled_skills = self._normalize_skill_names(enabled_skills or set())

    @staticmethod
    def _normalize_skill_names(values: set[str] | list[str]) -> set[str]:
        return {
            str(value).strip().casefold()
            for value in values
            if str(value).strip()
        }

    def discover(self, *, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """Compatibility alias for skill discovery."""
        return self.list_skills(filter_unavailable=filter_unavailable)

    def _source_roots(self) -> list[tuple[Path, str]]:
        roots = [
            (self.workspace_skills, "workspace"),
            (self.skills_dir, "configured"),
            (self.builtin_skills, "builtin"),
        ]
        ordered: list[tuple[Path, str]] = []
        seen: set[Path] = set()
        for path, source in roots:
            resolved = Path(path).expanduser().resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            ordered.append((resolved, source))
        return ordered

    def _is_enabled(self, normalized_name: str) -> bool:
        if normalized_name in self.disabled_skills:
            return False
        if self.enabled_skills and normalized_name not in self.enabled_skills:
            return False
        return True

    def _discover_paths(self) -> dict[str, tuple[Path, str]]:
        discovered: dict[str, tuple[Path, str]] = {}
        for root, source in self._source_roots():
            if not root.exists():
                continue
            for skill_dir in sorted(root.iterdir(), key=lambda item: item.name.lower()):
                if not skill_dir.is_dir():
                    continue
                skill_file = skill_dir / "SKILL.md"
                if not skill_file.is_file():
                    continue
                normalized = skill_dir.name.casefold()
                if not self._is_enabled(normalized) or normalized in discovered:
                    continue
                discovered[normalized] = (skill_file.resolve(), source)
        return discovered

    def resolve_skill(self, name: str) -> SkillSpec | None:
        normalized = str(name).strip().casefold()
        if not normalized or not self._is_enabled(normalized):
            return None

        discovered = self._discover_paths()
        resolved = discovered.get(normalized)
        if resolved is None:
            return None

        path, source = resolved
        display_name = path.parent.name
        meta = self._get_skill_meta_from_path(path)
        missing = "" if self._check_requirements(meta) else self._get_missing_requirements(meta)
        return SkillSpec(
            name=display_name,
            path=path,
            source=source,
            description=self._get_skill_description_from_path(path),
            available=not missing,
            missing_requirements=missing,
            always=bool(meta.get("always")),
        )

    def list_skill_specs(self, *, filter_unavailable: bool = True) -> list[SkillSpec]:
        specs: list[SkillSpec] = []
        for normalized_name, (path, source) in self._discover_paths().items():
            display_name = path.parent.name
            meta = self._get_skill_meta_from_path(path)
            missing = "" if self._check_requirements(meta) else self._get_missing_requirements(meta)
            spec = SkillSpec(
                name=display_name,
                path=path,
                source=source,
                description=self._get_skill_description_from_path(path),
                available=not missing,
                missing_requirements=missing,
                always=bool(meta.get("always")),
            )
            if filter_unavailable and not spec.available:
                continue
            specs.append(spec)
        return specs

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.

        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        return [
            {
                "name": spec.name,
                "path": str(spec.path),
                "source": spec.source,
            }
            for spec in self.list_skill_specs(filter_unavailable=filter_unavailable)
        ]

    @staticmethod
    def _read_skill_file(path: Path) -> str | None:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        spec = self.resolve_skill(name)
        if spec is None:
            return None
        return self._read_skill_file(spec.path)

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.

        Returns:
            Formatted skills content.
        """
        parts: list[str] = []
        seen: set[str] = set()
        for raw_name in skill_names:
            normalized = str(raw_name).strip().casefold()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            spec = self.resolve_skill(raw_name)
            if spec is None or not spec.available:
                continue
            markdown = self._read_skill_file(spec.path) or ""
            parts.append(f"### Skill: {spec.name}\n\n{self._strip_frontmatter(markdown)}")
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
        all_skills = self.list_skill_specs(filter_unavailable=False)
        if not all_skills:
            return ""

        hidden = self._normalize_skill_names(exclude or set())
        lines: list[str] = []

        for spec in all_skills:
            if spec.name.casefold() in hidden:
                continue
            source_tag = f" [{spec.source}]"
            if spec.available:
                lines.append(f"- **{spec.name}** - {spec.description}{source_tag}")
            else:
                suffix = (
                    f" (unavailable: {spec.missing_requirements})"
                    if spec.missing_requirements
                    else " (unavailable)"
                )
                lines.append(f"- **{spec.name}** - {spec.description}{suffix}{source_tag}")
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
        spec = self.resolve_skill(name)
        if spec is None:
            return False, "skill not found or disabled"
        return spec.available, spec.missing_requirements

    def get_skill_requirements(self, name: str) -> dict[str, list[str]]:
        """Return explicit command/env requirements and currently missing entries."""
        meta = self._get_skill_meta(name)
        requires = meta.get("requires", {})
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

    def _get_skill_description_from_path(self, path: Path) -> str:
        meta = self._get_skill_meta_from_path(path)
        if meta and meta.get("description"):
            return str(meta["description"])

        content = self._read_skill_file(path)
        if content:
            summary = self._extract_summary(content)
            if summary:
                return summary
        return path.parent.name

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

    def _get_skill_meta_from_path(self, path: Path) -> dict[str, Any]:
        raw_meta = self._get_skill_metadata_from_path(path) or {}
        parsed = self._parse_nanobot_metadata(raw_meta.get("metadata"))
        for key in ("always", "requires", "emoji", "install", "description"):
            if key in raw_meta and key not in parsed:
                parsed[key] = raw_meta[key]
        return parsed

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        return [
            spec.name
            for spec in self.list_skill_specs(filter_unavailable=True)
            if spec.always
        ]

    def get_skill_metadata(self, name: str) -> dict[str, Any] | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        spec = self.resolve_skill(name)
        if spec is None:
            return None
        return self._get_skill_metadata_from_path(spec.path)

    def _get_skill_metadata_from_path(self, path: Path) -> dict[str, Any] | None:
        content = self._read_skill_file(path)
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
