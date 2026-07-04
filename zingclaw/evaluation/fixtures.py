"""Fixture workspace helpers for offline task evaluation."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from zingclaw.evaluation.task_contract import TaskContract

WorkspaceEntryKind = Literal["file", "dir"]


def _normal_relpath(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(slots=True)
class WorkspaceEntry:
    """One file or directory captured in a workspace snapshot."""

    path: str
    kind: WorkspaceEntryKind
    size: int = 0
    sha256: str = ""

    @classmethod
    def from_path(cls, root: Path, path: Path) -> "WorkspaceEntry":
        relpath = _normal_relpath(root, path)
        if path.is_dir():
            return cls(path=relpath, kind="dir")
        if path.is_file():
            return cls(
                path=relpath,
                kind="file",
                size=path.stat().st_size,
                sha256=_file_sha256(path),
            )
        raise ValueError(f"unsupported workspace entry: {path}")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": self.path, "kind": self.kind}
        if self.kind == "file":
            payload["size"] = self.size
            payload["sha256"] = self.sha256
        return payload


@dataclass(slots=True)
class WorkspaceSnapshot:
    """A deterministic snapshot of a fixture workspace."""

    root: Path
    entries: dict[str, WorkspaceEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "entries": [entry.to_dict() for entry in sorted(self.entries.values(), key=lambda item: item.path)],
        }


@dataclass(slots=True)
class WorkspaceDiff:
    """File-level diff between two workspace snapshots."""

    created: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created or self.modified or self.deleted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": list(self.created),
            "modified": list(self.modified),
            "deleted": list(self.deleted),
            "unchanged": list(self.unchanged),
            "changed": self.changed,
        }


@dataclass(slots=True)
class TaskWorkspace:
    """Prepared isolated workspace for one task contract run."""

    task_id: str
    run_id: str
    fixture_name: str
    fixture_workspace: Path
    run_dir: Path
    workspace_dir: Path
    before: WorkspaceSnapshot

    def snapshot_after(self) -> WorkspaceSnapshot:
        return snapshot_workspace(self.workspace_dir)

    def diff(self) -> WorkspaceDiff:
        return diff_snapshots(self.before, self.snapshot_after())

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "fixture": self.fixture_name,
            "fixture_workspace": str(self.fixture_workspace),
            "run_dir": str(self.run_dir),
            "workspace_dir": str(self.workspace_dir),
            "before": self.before.to_dict(),
        }


def snapshot_workspace(root: Path) -> WorkspaceSnapshot:
    """Capture files and directories below a workspace root."""
    workspace = Path(root).resolve(strict=False)
    if not workspace.exists():
        raise FileNotFoundError(workspace)
    if not workspace.is_dir():
        raise NotADirectoryError(workspace)
    entries: dict[str, WorkspaceEntry] = {}
    for item in sorted(workspace.rglob("*")):
        if item.is_file() or item.is_dir():
            entry = WorkspaceEntry.from_path(workspace, item)
            entries[entry.path] = entry
    return WorkspaceSnapshot(root=workspace, entries=entries)


def diff_snapshots(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> WorkspaceDiff:
    """Compare two workspace snapshots."""
    before_paths = set(before.entries)
    after_paths = set(after.entries)
    created = sorted(after_paths - before_paths)
    deleted = sorted(before_paths - after_paths)
    modified: list[str] = []
    unchanged: list[str] = []
    for path in sorted(before_paths & after_paths):
        old = before.entries[path]
        new = after.entries[path]
        if old.kind != new.kind or old.size != new.size or old.sha256 != new.sha256:
            modified.append(path)
        else:
            unchanged.append(path)
    return WorkspaceDiff(created=created, modified=modified, deleted=deleted, unchanged=unchanged)


def _default_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _fixture_workspace_dir(fixtures_root: Path, fixture_name: str) -> Path:
    fixture_dir = (Path(fixtures_root) / fixture_name).resolve(strict=False)
    workspace_dir = fixture_dir / "workspace"
    if not workspace_dir.exists():
        raise FileNotFoundError(f"fixture workspace not found: {workspace_dir}")
    if not workspace_dir.is_dir():
        raise NotADirectoryError(workspace_dir)
    try:
        workspace_dir.relative_to(Path(fixtures_root).resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"fixture {fixture_name!r} escapes fixtures root") from exc
    return workspace_dir


def prepare_task_workspace(
    contract: TaskContract,
    *,
    fixtures_root: Path,
    runs_root: Path,
    run_id: str | None = None,
) -> TaskWorkspace:
    """Copy a task fixture into an isolated run workspace."""
    actual_run_id = run_id or _default_run_id()
    fixture_workspace = _fixture_workspace_dir(fixtures_root, contract.fixture)
    run_dir = (Path(runs_root) / actual_run_id / "tasks" / contract.id).resolve(strict=False)
    workspace_dir = run_dir / "workspace"
    if workspace_dir.exists():
        raise FileExistsError(f"task workspace already exists: {workspace_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fixture_workspace, workspace_dir)
    return TaskWorkspace(
        task_id=contract.id,
        run_id=actual_run_id,
        fixture_name=contract.fixture,
        fixture_workspace=fixture_workspace,
        run_dir=run_dir,
        workspace_dir=workspace_dir,
        before=snapshot_workspace(workspace_dir),
    )
