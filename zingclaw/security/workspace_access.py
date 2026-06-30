"""Workspace access policies reused by filesystem and shell tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from zingclaw.security.workspace_policy import (
    WORKSPACE_BOUNDARY_NOTE,
    is_path_allowed,
    resolve_allowed_path,
    resolve_path,
)


def _coerce_paths(paths: Iterable[str | Path] | None) -> tuple[Path, ...]:
    return tuple(Path(path).expanduser().resolve(strict=False) for path in (paths or ()))


@dataclass(frozen=True, slots=True)
class WorkspaceAccessPolicy:
    """Resolved workspace access rules for one tool instance."""

    workspace_root: Path
    restrict_to_workspace: bool = True
    allowed_root: Path | None = None
    extra_allowed_roots: tuple[Path, ...] = ()
    extra_allowed_files: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        workspace_root = Path(self.workspace_root).expanduser().resolve(strict=False)
        object.__setattr__(self, "workspace_root", workspace_root)

        allowed_root = self.allowed_root
        if allowed_root is None and self.restrict_to_workspace:
            allowed_root = workspace_root
        if allowed_root is not None:
            allowed_root = Path(allowed_root).expanduser().resolve(strict=False)
        object.__setattr__(self, "allowed_root", allowed_root)
        object.__setattr__(self, "extra_allowed_roots", _coerce_paths(self.extra_allowed_roots))
        object.__setattr__(self, "extra_allowed_files", _coerce_paths(self.extra_allowed_files))

    def resolve_path(
        self,
        path: str | Path,
        *,
        strict: bool = False,
        message: str | None = None,
    ) -> Path:
        """Resolve a path using the current boundary rules."""
        if not self.restrict_to_workspace and self.allowed_root is None:
            return resolve_path(path, self.workspace_root, strict=strict)
        return resolve_allowed_path(
            path,
            workspace=self.workspace_root,
            allowed_root=self.allowed_root,
            extra_allowed_roots=self.extra_allowed_roots,
            extra_allowed_files=self.extra_allowed_files,
            strict=strict,
            message=message,
        )

    def validate_working_dir(self, working_dir: str | Path | None = None) -> Path:
        """Resolve and validate a shell working directory."""
        candidate = working_dir or self.workspace_root
        if not self.restrict_to_workspace and self.allowed_root is None:
            return resolve_path(candidate, self.workspace_root, strict=False)
        return self.resolve_path(
            candidate,
            message="working_dir is outside the configured workspace" + WORKSPACE_BOUNDARY_NOTE,
        )

    def allowed_command_roots(self, cwd: str | Path | None = None) -> tuple[Path, ...]:
        """Return roots that shell absolute paths may legally touch."""
        roots: list[Path] = []
        if cwd is not None:
            roots.append(resolve_path(cwd, self.workspace_root, strict=False))
        if self.allowed_root is not None:
            roots.append(self.allowed_root)
        roots.extend(self.extra_allowed_roots)

        unique: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root).lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(root)
        return tuple(unique)

    def allows_command_path(self, path: str | Path, *, cwd: str | Path | None = None) -> bool:
        """Return whether an absolute shell path stays within allowed roots."""
        if not self.restrict_to_workspace:
            return True
        roots = self.allowed_command_roots(cwd)
        if not roots:
            return True
        return is_path_allowed(path, roots)
