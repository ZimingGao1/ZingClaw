"""Structured multi-file patch tool for code edits."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zingclaw.tools.base import tool_parameters
from zingclaw.tools.filesystem import _FsTool


@dataclass(slots=True)
class _PatchSummary:
    action: str
    path: str
    added: int = 0
    deleted: int = 0


class _PatchError(ValueError):
    """User-facing patch validation error."""


def _validate_patch_path(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        raise _PatchError("patch path cannot be empty")
    if "\0" in normalized:
        raise _PatchError(f"patch path contains a null byte: {path!r}")
    return normalized


def _lines_to_text(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _text_line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def _line_diff_stats(before: str, after: str) -> tuple[int, int]:
    before_lines = before.replace("\r\n", "\n").splitlines()
    after_lines = after.replace("\r\n", "\n").splitlines()
    added = 0
    deleted = 0
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in {"replace", "delete"}:
            deleted += i2 - i1
        if tag in {"replace", "insert"}:
            added += j2 - j1
    return added, deleted


def _append_text(content: str, addition: str) -> str:
    """Append text without merging it into an unterminated final line."""
    base = content.replace("\r\n", "\n")
    extra = addition.replace("\r\n", "\n")
    if base and extra and not base.endswith("\n") and not extra.startswith("\n"):
        base += "\n"
    combined = base + extra
    if combined and not combined.endswith("\n"):
        combined += "\n"
    return combined


def _format_summary(summary: _PatchSummary) -> str:
    stats = ""
    if summary.added or summary.deleted:
        stats = f" (+{summary.added}/-{summary.deleted})"
    return f"- {summary.action} {summary.path}{stats}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "description": "List of edits to apply across one or more files.",
                "minItems": 1,
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Path to the file to edit. Relative paths resolve against the "
                                "workspace and still obey the workspace boundary policy."
                            ),
                            "minLength": 1,
                        },
                        "action": {
                            "type": "string",
                            "description": "Operation type: replace or add.",
                            "enum": ["replace", "add"],
                        },
                        "old_text": {
                            "type": ["string", "null"],
                            "description": "Exact text to search for in the file. Required for replace.",
                        },
                        "new_text": {
                            "type": ["string", "null"],
                            "description": "Replacement text or text to append/create.",
                        },
                    },
                    "required": ["path", "action"],
                    "additionalProperties": False,
                },
            },
            "dry_run": {
                "type": "boolean",
                "description": "Validate and summarize the patch without writing files.",
                "default": False,
            },
        },
        "required": ["edits"],
        "additionalProperties": False,
    }
)
class ApplyPatchTool(_FsTool):
    """Apply multi-file code edits using structured patch instructions."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "apply_patch"

    @property
    def description(self) -> str:
        return (
            "Default tool for code edits. Supports multi-file changes in a single call. "
            "Provide structured edits with path, action, and exact text to change. "
            "Set dry_run=true to validate and preview without writing files. "
            "Use edit_file only for small exact replacements on a single file."
        )

    async def execute(
        self,
        edits: list[dict[str, Any]] | None = None,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> str:
        del kwargs
        try:
            if not edits:
                raise _PatchError("must provide edits")

            writes: dict[Path, str] = {}
            summaries: list[_PatchSummary] = []

            for edit in edits:
                if not isinstance(edit, dict):
                    raise _PatchError("each edit must be an object")

                raw_path = edit.get("path")
                if not isinstance(raw_path, str):
                    raise _PatchError("path required for edit")
                path = _validate_patch_path(raw_path)

                action = edit.get("action")
                if not isinstance(action, str):
                    raise _PatchError(f"action required for edit: {path}")

                source = self._resolve_write(path)

                if action == "add":
                    new_text = edit.get("new_text")
                    if new_text is None:
                        raise _PatchError(f"new_text required for add: {path}")
                    if not isinstance(new_text, str):
                        raise _PatchError(f"new_text must be a string for add: {path}")

                    pending = writes.get(source)
                    if pending is not None:
                        content = pending
                        exists = True
                    elif source.exists():
                        raw = source.read_bytes()
                        try:
                            content = raw.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise _PatchError(f"file is not UTF-8 text: {path}") from exc
                        exists = True
                    else:
                        content = ""
                        exists = False

                    if exists:
                        uses_crlf = "\r\n" in content
                        new_norm = _append_text(content, new_text)
                        if uses_crlf:
                            new_norm = new_norm.replace("\n", "\r\n")
                        writes[source] = new_norm
                        added, deleted = _line_diff_stats(content, new_norm)
                        action_name = "update"
                    else:
                        new_norm = new_text.replace("\r\n", "\n")
                        if new_norm and not new_norm.endswith("\n"):
                            new_norm += "\n"
                        writes[source] = new_norm
                        added = _text_line_count(new_norm)
                        deleted = 0
                        action_name = "add"

                    summaries.append(
                        _PatchSummary(action=action_name, path=path, added=added, deleted=deleted)
                    )
                    continue

                if action == "replace":
                    old_text = edit.get("old_text") or ""
                    if not isinstance(old_text, str) or not old_text:
                        raise _PatchError(f"old_text required for replace: {path}")
                    new_text = edit.get("new_text")
                    if new_text is None:
                        raise _PatchError(f"new_text required for replace: {path}")
                    if not isinstance(new_text, str):
                        raise _PatchError(f"new_text must be a string for replace: {path}")

                    pending = writes.get(source)
                    if pending is not None:
                        content = pending
                    elif source.exists():
                        raw = source.read_bytes()
                        try:
                            content = raw.decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise _PatchError(f"file is not UTF-8 text: {path}") from exc
                    else:
                        raise _PatchError(f"file to update does not exist: {path}")

                    if pending is None and not source.is_file():
                        raise _PatchError(f"path to update is not a file: {path}")

                    uses_crlf = "\r\n" in content
                    normalized_content = content.replace("\r\n", "\n")
                    normalized_old = old_text.replace("\r\n", "\n")

                    pos = normalized_content.find(normalized_old)
                    if pos < 0:
                        raise _PatchError(f"old_text not found in {path}")
                    if normalized_content.find(normalized_old, pos + 1) >= 0:
                        raise _PatchError(f"old_text appears multiple times in {path}")

                    new_norm = (
                        normalized_content[:pos]
                        + new_text.replace("\r\n", "\n")
                        + normalized_content[pos + len(normalized_old) :]
                    )
                    if new_norm and not new_norm.endswith("\n"):
                        new_norm += "\n"
                    if uses_crlf:
                        new_norm = new_norm.replace("\n", "\r\n")

                    writes[source] = new_norm
                    added, deleted = _line_diff_stats(content, new_norm)
                    summaries.append(
                        _PatchSummary(action="update", path=path, added=added, deleted=deleted)
                    )
                    continue

                raise _PatchError(f"unknown action: {action}")

            if dry_run:
                return self.ok("Patch dry-run succeeded:\n" + "\n".join(
                    _format_summary(summary) for summary in summaries
                ))

            backups: dict[Path, bytes | None] = {
                path: path.read_bytes() if path.exists() else None for path in writes
            }
            try:
                for path, content in writes.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8", newline="")
            except Exception:
                for path, data in backups.items():
                    if data is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                raise

            return self.ok("Patch applied:\n" + "\n".join(_format_summary(summary) for summary in summaries))
        except PermissionError as exc:
            return self.error(str(exc))
        except _PatchError as exc:
            return self.error(f"applying patch failed: {exc}")
        except Exception as exc:
            return self.error(f"applying patch failed: {exc}")
