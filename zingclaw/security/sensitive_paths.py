"""Shared helpers for blocking access to sensitive user/system locations."""

from __future__ import annotations

import os
from pathlib import Path


def _home_dir() -> Path:
    return Path.home().expanduser().resolve(strict=False)


def _norm(path: Path) -> str:
    return os.path.normcase(os.fspath(path))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def sensitive_roots() -> list[tuple[Path, str]]:
    """Return default sensitive root directories for the current platform."""
    home = _home_dir()
    roots: list[tuple[Path, str]] = [
        (home / ".ssh", "SSH keys"),
        (home / ".gnupg", "GnuPG keys"),
        (home / ".aws", "AWS credentials"),
        (home / ".azure", "Azure credentials"),
        (home / ".kube", "Kubernetes credentials"),
        (home / ".docker", "Docker credentials"),
        (home / ".config" / "gcloud", "Google Cloud credentials"),
        (home / ".config" / "gh", "GitHub CLI credentials"),
    ]

    appdata = home / "AppData"
    roots.extend(
        [
            (appdata / "Roaming" / "gnupg", "GnuPG keys"),
            (appdata / "Roaming" / "kube", "Kubernetes credentials"),
            (appdata / "Local" / "Google" / "Chrome" / "User Data", "browser profile data"),
            (appdata / "Roaming" / "Mozilla" / "Firefox" / "Profiles", "browser profile data"),
        ]
    )
    return roots


def sensitive_files() -> list[tuple[Path, str]]:
    """Return default exact sensitive files for the current platform."""
    home = _home_dir()
    return [
        (home / ".env", "user-level environment secrets"),
        (home / ".git-credentials", "Git credentials"),
        (home / ".npmrc", "npm credentials"),
        (home / ".pypirc", "PyPI credentials"),
        (home / ".netrc", "network credentials"),
        (home / "_netrc", "network credentials"),
    ]


def sensitive_path_reason(path: str | Path, *, workspace: str | Path | None = None) -> str | None:
    """Return a human-readable reason when a path is considered sensitive."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and workspace is not None:
        candidate = Path(workspace).expanduser() / candidate
    logical = Path(os.path.abspath(candidate))

    if workspace is not None:
        workspace_root = Path(workspace).expanduser().resolve(strict=False)
        if _is_within(logical, workspace_root):
            return None

    logical_key = _norm(logical)
    for exact_file, reason in sensitive_files():
        if _norm(exact_file) == logical_key:
            return reason

    for root, reason in sensitive_roots():
        if _is_within(logical, root):
            return reason
    return None
