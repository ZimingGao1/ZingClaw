"""Minimal filesystem tools for ZingClaw's P0 runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zingclaw.tools.base import Tool, tool_parameters

_DEFAULT_MAX_RESULT_CHARS = 16_000


@dataclass(slots=True)
class _MatchSpan:
    start: int
    end: int
    text: str
    line: int


def _resolve_path(raw_path: str, *, base_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _coerce_directory_list(paths: list[Path] | None) -> list[Path]:
    return [Path(path).expanduser().resolve(strict=False) for path in (paths or [])]


def _coerce_file_list(paths: list[Path] | None) -> list[Path]:
    return [Path(path).expanduser().resolve(strict=False) for path in (paths or [])]


def _find_exact_matches(content: str, old_text: str) -> list[_MatchSpan]:
    matches: list[_MatchSpan] = []
    if not old_text:
        return matches

    start = 0
    while True:
        index = content.find(old_text, start)
        if index == -1:
            break
        end = index + len(old_text)
        line = content.count("\n", 0, index) + 1
        matches.append(_MatchSpan(start=index, end=end, text=content[index:end], line=line))
        start = end
    return matches


class _FsTool(Tool):
    """Shared base for filesystem tools with workspace-aware path resolution."""

    config_key = "file"
    _scopes = {"core"}

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        extra_allowed_dirs: list[Path] | None = None,
        extra_read_allowed_dirs: list[Path] | None = None,
        extra_write_allowed_dirs: list[Path] | None = None,
        extra_write_allowed_files: list[Path] | None = None,
        restrict_to_workspace: bool | None = None,
        max_result_chars: int = _DEFAULT_MAX_RESULT_CHARS,
    ) -> None:
        self._workspace = Path(workspace or Path.cwd()).expanduser().resolve(strict=False)
        default_allowed_dir = allowed_dir
        if default_allowed_dir is None and restrict_to_workspace is not False:
            default_allowed_dir = self._workspace

        self._allowed_dir = (
            Path(default_allowed_dir).expanduser().resolve(strict=False)
            if default_allowed_dir is not None
            else None
        )
        self._restrict_to_workspace = (
            restrict_to_workspace if restrict_to_workspace is not None else self._allowed_dir is not None
        )
        self._extra_read_allowed_dirs = _coerce_directory_list(
            [*(extra_allowed_dirs or []), *(extra_read_allowed_dirs or [])]
        )
        self._extra_write_allowed_dirs = _coerce_directory_list(extra_write_allowed_dirs)
        self._extra_write_allowed_files = _coerce_file_list(extra_write_allowed_files)
        self._max_result_chars = max(512, int(max_result_chars))

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            workspace=Path(ctx.workspace),
            allowed_dir=Path(ctx.workspace),
            restrict_to_workspace=True,
            max_result_chars=int(getattr(ctx, "max_tool_result_chars", _DEFAULT_MAX_RESULT_CHARS)),
        )

    def _resolve_with_policy(
        self,
        path: str,
        *,
        extra_allowed_dirs: list[Path] | None = None,
        extra_allowed_files: list[Path] | None = None,
    ) -> Path:
        candidate = _resolve_path(path, base_dir=self._workspace)
        allowed_dirs: list[Path] = []
        if self._restrict_to_workspace and self._allowed_dir is not None:
            allowed_dirs.append(self._allowed_dir)
        allowed_dirs.extend(extra_allowed_dirs or [])

        if not allowed_dirs and not extra_allowed_files:
            return candidate

        if any(_is_within(candidate, root) for root in allowed_dirs):
            return candidate
        if any(candidate == allowed_file for allowed_file in (extra_allowed_files or [])):
            return candidate

        raise PermissionError(
            f"Path '{path}' resolves outside the allowed workspace boundary: {candidate}"
        )

    def _resolve_read(self, path: str) -> Path:
        return self._resolve_with_policy(path, extra_allowed_dirs=self._extra_read_allowed_dirs)

    def _resolve_write(self, path: str) -> Path:
        return self._resolve_with_policy(
            path,
            extra_allowed_dirs=self._extra_write_allowed_dirs,
            extra_allowed_files=self._extra_write_allowed_files,
        )

    def _truncate_text(self, text: str, *, suffix: str | None = None) -> str:
        if len(text) <= self._max_result_chars:
            return text
        reserve = len(suffix) if suffix else 0
        budget = max(0, self._max_result_chars - reserve - 32)
        trimmed = text[:budget].rstrip()
        note = f"\n\n[output truncated to {self._max_result_chars} chars]"
        return trimmed + note + (suffix or "")


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to list", "default": "."},
            "recursive": {
                "type": "boolean",
                "description": "Recursively list nested files and directories",
                "default": False,
            },
            "max_entries": {
                "type": "integer",
                "description": "Maximum entries to return",
                "minimum": 1,
                "default": 200,
            },
        },
        "additionalProperties": False,
    }
)
class ListDirTool(_FsTool):
    """List directory contents."""

    _DEFAULT_MAX_ENTRIES = 200
    _IGNORE_DIRS = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
    }

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. "
            "Use recursive=true to inspect nested structure."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        path: str = ".",
        recursive: bool = False,
        max_entries: int | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        try:
            directory = self._resolve_read(path)
            if not directory.exists():
                return f"Error: Directory not found: {path}"
            if not directory.is_dir():
                return f"Error: Not a directory: {path}"

            cap = max_entries or self._DEFAULT_MAX_ENTRIES
            items: list[str] = []
            total = 0

            if recursive:
                iterator = sorted(
                    directory.rglob("*"),
                    key=lambda item: str(item.relative_to(directory)).lower(),
                )
                for item in iterator:
                    rel = item.relative_to(directory)
                    if any(part in self._IGNORE_DIRS for part in rel.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        items.append(f"{rel.as_posix()}/" if item.is_dir() else rel.as_posix())
            else:
                iterator = sorted(directory.iterdir(), key=lambda item: item.name.lower())
                for item in iterator:
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        items.append(f"{item.name}/" if item.is_dir() else item.name)

            if total == 0:
                return f"Directory is empty: {path}"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n[truncated: showing first {cap} of {total} entries]"
            return self._truncate_text(result)
        except PermissionError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error listing directory: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
            "start_line": {
                "type": "integer",
                "description": "1-based starting line number",
                "minimum": 1,
            },
            "end_line": {
                "type": "integer",
                "description": "1-based ending line number, inclusive",
                "minimum": 1,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }
)
class ReadFileTool(_FsTool):
    """Read UTF-8 text files with line-numbered output."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a UTF-8 text file. "
            "Output is line-numbered as LINE| CONTENT. "
            "Use start_line and end_line to page through large files."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        path: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        try:
            if not path:
                raise ValueError("Unknown path")
            if start_line is not None and start_line < 1:
                return "Error: start_line must be >= 1"
            if end_line is not None and end_line < 1:
                return "Error: end_line must be >= 1"
            if start_line is not None and end_line is not None and end_line < start_line:
                return "Error: end_line must be >= start_line"

            file_path = self._resolve_read(path)
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"

            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return f"Error: File is not valid UTF-8 text: {path}"

            if content == "":
                return f"Empty file: {path}"

            lines = content.splitlines()
            if not lines:
                return f"Empty file: {path}"

            total_lines = len(lines)
            start = start_line or 1
            if start > total_lines:
                return f"Error: start_line {start} is beyond end of file ({total_lines} lines)"
            end = min(end_line or total_lines, total_lines)

            selected = lines[start - 1 : end]
            footer = (
                f"\n\n[Use start_line={end + 1} to continue. File has {total_lines} lines total.]"
                if end < total_lines
                else f"\n\n[End of file. Total lines: {total_lines}]"
            )

            budget = max(256, self._max_result_chars - len(footer) - 64)
            rendered: list[str] = []
            last_line_number = start - 1
            current_size = 0

            for offset, line in enumerate(selected, start=start):
                row = f"{offset}| {line}"
                row_size = len(row) + (1 if rendered else 0)
                if rendered and current_size + row_size > budget:
                    break
                if not rendered and len(row) > budget:
                    rendered.append(row[:budget].rstrip())
                    current_size = len(rendered[0])
                    last_line_number = offset
                    break
                rendered.append(row)
                current_size += row_size
                last_line_number = offset

            body = "\n".join(rendered)
            truncated = last_line_number < end
            if truncated:
                footer = (
                    f"\n\n[output truncated at line {last_line_number}. "
                    f"Use start_line={last_line_number + 1} to continue. "
                    f"File has {total_lines} lines total.]"
                )

            return body + footer
        except PermissionError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error reading file: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "UTF-8 text content to write"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }
)
class WriteFileTool(_FsTool):
    """Write UTF-8 text content to a file."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Create or replace a UTF-8 text file. "
            "Creates parent directories as needed."
        )

    async def execute(self, path: str | None = None, content: str | None = None, **kwargs: Any) -> str:
        del kwargs
        try:
            if not path:
                raise ValueError("Unknown path")
            if content is None:
                raise ValueError("Unknown content")

            file_path = self._resolve_write(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} characters to {file_path}"
        except PermissionError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error writing file: {exc}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to edit"},
            "old_text": {"type": "string", "description": "Existing text to replace"},
            "new_text": {"type": "string", "description": "Replacement text"},
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences instead of exactly one",
                "default": False,
            },
            "occurrence": {
                "type": ["integer", "null"],
                "description": "1-based occurrence to replace when old_text appears multiple times",
                "minimum": 1,
            },
            "line_hint": {
                "type": ["integer", "null"],
                "description": "Nearest 1-based line to use when old_text appears multiple times",
                "minimum": 1,
            },
            "expected_replacements": {
                "type": ["integer", "null"],
                "description": "Guard for how many replacements must be made",
                "minimum": 1,
            },
        },
        "required": ["path", "old_text", "new_text"],
        "additionalProperties": False,
    }
)
class EditFileTool(_FsTool):
    """Edit a UTF-8 text file by replacing exact text."""

    _MAX_EDIT_FILE_SIZE = 10 * 1024 * 1024

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Replace exact text in a UTF-8 file. "
            "Use text copied from read_file. "
            "If the text appears multiple times, set occurrence, line_hint, or replace_all."
        )

    async def execute(
        self,
        path: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False,
        occurrence: int | None = None,
        line_hint: int | None = None,
        expected_replacements: int | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        try:
            if not path:
                raise ValueError("Unknown path")
            if old_text is None:
                raise ValueError("Unknown old_text")
            if new_text is None:
                raise ValueError("Unknown new_text")
            if occurrence is not None and occurrence < 1:
                return "Error: occurrence must be >= 1."
            if line_hint is not None and line_hint < 1:
                return "Error: line_hint must be >= 1."
            if expected_replacements is not None and expected_replacements < 1:
                return "Error: expected_replacements must be >= 1."
            if replace_all and occurrence is not None:
                return "Error: occurrence cannot be used with replace_all=true."
            if replace_all and line_hint is not None:
                return "Error: line_hint cannot be used with replace_all=true."
            if occurrence is not None and line_hint is not None:
                return "Error: line_hint cannot be used with occurrence."

            file_path = self._resolve_write(path)

            if not file_path.exists():
                if old_text == "":
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(new_text, encoding="utf-8")
                    return f"Successfully created {file_path}"
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"

            try:
                file_size = file_path.stat().st_size
            except OSError:
                file_size = 0
            if file_size > self._MAX_EDIT_FILE_SIZE:
                return (
                    f"Error: File too large to edit ({file_size} bytes). "
                    f"Maximum is {self._MAX_EDIT_FILE_SIZE} bytes."
                )

            raw = file_path.read_bytes()
            uses_crlf = b"\r\n" in raw
            try:
                content = raw.decode("utf-8").replace("\r\n", "\n")
            except UnicodeDecodeError:
                return f"Error: File is not valid UTF-8 text: {path}"

            if old_text == "":
                if content.strip():
                    return f"Error: Cannot create file - {path} already exists and is not empty."
                file_path.write_text(new_text, encoding="utf-8")
                return f"Successfully edited {file_path}"

            normalized_old = old_text.replace("\r\n", "\n")
            normalized_new = new_text.replace("\r\n", "\n")
            matches = _find_exact_matches(content, normalized_old)
            if not matches:
                return f"Error: old_text not found in {path}."

            count = len(matches)
            if count > 1 and not replace_all:
                if occurrence is not None:
                    if occurrence > count:
                        return (
                            f"Error: occurrence {occurrence} is out of range; "
                            f"old_text appears {count} times."
                        )
                elif line_hint is not None:
                    nearest = min(matches, key=lambda match: abs(match.line - line_hint))
                    distance = abs(nearest.line - line_hint)
                    if sum(1 for match in matches if abs(match.line - line_hint) == distance) > 1:
                        return (
                            f"Error: line_hint {line_hint} is ambiguous; "
                            f"old_text appears {count} times."
                        )
                else:
                    line_numbers = ", ".join(f"line {match.line}" for match in matches[:3])
                    if count > 3:
                        line_numbers += ", ..."
                    return (
                        f"Warning: old_text appears {count} times at {line_numbers}. "
                        "Provide more context, set occurrence to choose one match, or set replace_all=true."
                    )
            elif occurrence is not None and occurrence > count:
                return (
                    f"Error: occurrence {occurrence} is out of range; "
                    f"old_text appears {count} time."
                )

            if replace_all:
                selected = matches
            elif line_hint is not None:
                selected = [min(matches, key=lambda match: abs(match.line - line_hint))]
            else:
                selected = [matches[occurrence - 1 if occurrence else 0]]

            if expected_replacements is not None and len(selected) != expected_replacements:
                return (
                    f"Error: expected {expected_replacements} replacements but would make {len(selected)}."
                )

            new_content = content
            for match in reversed(selected):
                end = match.end
                if normalized_new == "" and not match.text.endswith("\n") and content[end : end + 1] == "\n":
                    end += 1
                new_content = new_content[: match.start] + normalized_new + new_content[end:]

            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            file_path.write_bytes(new_content.encode("utf-8"))
            return f"Successfully edited {file_path}"
        except PermissionError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error editing file: {exc}"
