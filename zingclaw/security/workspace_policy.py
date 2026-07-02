"""Shared workspace boundary helpers for path-based tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from zingclaw.security.sensitive_paths import sensitive_path_reason

WORKSPACE_BOUNDARY_NOTE = (
    "\n\nNote: this is a hard policy boundary, not a transient failure. "
    "Do NOT retry with shell tricks or path workarounds. "
    "If the user really needs this resource, ask how they want to proceed."
)
SENSITIVE_PATH_NOTE = (
    "\n\nNote: this path looks like a sensitive credential/profile location "
    "(for example SSH keys, cloud auth, kube config, or browser profile data). "
    "Do NOT try alternate commands to reach it unless the user explicitly wants that."
)


class WorkspaceBoundaryError(PermissionError):
    """Raised when a path escapes the configured workspace boundary."""


def resolve_path(
    path: str | Path,
    workspace: str | Path | None = None,
    *,
    strict: bool = False,
) -> Path:
    """Resolve a path, interpreting relative paths against the workspace root."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and workspace is not None:
        candidate = Path(workspace).expanduser() / candidate
    return candidate.resolve(strict=strict)


def _resolve_logical_path(path: str | Path, workspace: str | Path | None = None) -> Path:
    """Return an absolute normalized path without following symlinks."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and workspace is not None:
        candidate = Path(workspace).expanduser() / candidate
    return Path(os.path.abspath(candidate))


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.fspath(path))


def is_path_within(path: str | Path, root: str | Path) -> bool:
    """Return True when *path* resolves to *root* or one of its descendants."""
    try:
        resolved_path = Path(path).expanduser().resolve(strict=False)
        resolved_root = Path(root).expanduser().resolve(strict=False)
        resolved_path.relative_to(resolved_root)
        return True
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def is_path_allowed(path: str | Path, roots: Iterable[str | Path]) -> bool:
    """Return True when *path* is within any allowed root."""
    return any(is_path_within(path, root) for root in roots)


def _is_path_exactly_allowed(
    logical_path: Path,
    resolved_path: Path,
    files: Iterable[str | Path],
) -> bool:
    logical_key = _path_key(logical_path)
    if _path_key(resolved_path) != logical_key:
        return False
    for file in files:
        try:
            allowed_file = _resolve_logical_path(file)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if _path_key(allowed_file) == logical_key:
            return True
    return False


def resolve_allowed_path(
    path: str | Path,
    *,
    workspace: str | Path | None = None,
    allowed_root: str | Path | None = None,
    extra_allowed_roots: Iterable[str | Path] | None = None,
    extra_allowed_files: Iterable[str | Path] | None = None,
    strict: bool = False,
    message: str | None = None,
) -> Path:
    """Resolve a path and require it to stay within the configured boundary."""
    resolved = resolve_path(path, workspace, strict=False)
    sensitive_reason = sensitive_path_reason(path, workspace=workspace)
    files = list(extra_allowed_files or [])
    if allowed_root is None and not files and not list(extra_allowed_roots or []):
        if sensitive_reason:
            raise WorkspaceBoundaryError(
                message
                or (
                    f"Path '{path}' resolves to a sensitive location "
                    f"({sensitive_reason}) and is blocked by policy: {resolved}"
                    + SENSITIVE_PATH_NOTE
                    + WORKSPACE_BOUNDARY_NOTE
                )
            )
        return resolve_path(path, workspace, strict=strict) if strict else resolved

    roots: list[str | Path] = []
    if allowed_root is not None:
        roots.append(allowed_root)
    roots.extend(extra_allowed_roots or [])

    exact_allowed = bool(files) and _is_path_exactly_allowed(
        _resolve_logical_path(path, workspace),
        resolved,
        files,
    )
    allowed = is_path_allowed(resolved, roots) or exact_allowed
    if sensitive_reason and not allowed:
        raise WorkspaceBoundaryError(
            message
            or (
                f"Path '{path}' resolves to a sensitive location "
                f"({sensitive_reason}) and is blocked by policy: {resolved}"
                + SENSITIVE_PATH_NOTE
                + WORKSPACE_BOUNDARY_NOTE
            )
        )
    if not allowed:
        raise WorkspaceBoundaryError(
            message
            or f"Path '{path}' resolves outside the allowed workspace boundary: {resolved}"
            + WORKSPACE_BOUNDARY_NOTE
        )

    if strict:
        return resolve_path(path, workspace, strict=True)
    return resolved
