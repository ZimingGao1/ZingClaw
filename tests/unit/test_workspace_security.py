from __future__ import annotations

import sys
from pathlib import Path

import pytest

from zingclaw.config import Settings
from zingclaw.security import WorkspaceAccessPolicy, WorkspaceBoundaryError, resolve_allowed_path
from zingclaw.tools import ExecTool, ReadFileTool, ToolContext


def _cwd_command() -> str:
    return "cd" if sys.platform == "win32" else "pwd"


def test_resolve_allowed_path_blocks_workspace_escape_with_shared_error_message(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(WorkspaceBoundaryError) as exc_info:
        resolve_allowed_path(str(outside / "secret.txt"), workspace=workspace, allowed_root=workspace)

    assert "outside the allowed workspace boundary" in str(exc_info.value)
    assert "hard policy boundary" in str(exc_info.value)


def test_workspace_access_policy_allows_explicit_extra_file_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed_file = outside / "allowed.txt"
    allowed_file.write_text("ok", encoding="utf-8")
    blocked_file = outside / "blocked.txt"
    blocked_file.write_text("nope", encoding="utf-8")

    policy = WorkspaceAccessPolicy(
        workspace_root=workspace,
        restrict_to_workspace=True,
        allowed_root=workspace,
        extra_allowed_files=(allowed_file,),
    )

    assert policy.resolve_path(str(allowed_file)) == allowed_file.resolve()
    with pytest.raises(WorkspaceBoundaryError):
        policy.resolve_path(str(blocked_file))


@pytest.mark.asyncio
async def test_read_file_create_uses_security_setting_to_disable_workspace_restriction(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    settings = Settings(_env_file=None, home_dir=home, security={"restrict_to_workspace": False})

    tool = ReadFileTool.create(ToolContext.from_settings(settings))
    result = await tool.execute(path=str(secret))

    assert "1| top secret" in result
    assert "outside the allowed workspace boundary" not in result


@pytest.mark.asyncio
async def test_exec_tool_create_uses_security_setting_to_allow_workdir_outside_workspace(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    settings = Settings(_env_file=None, home_dir=home, security={"restrict_to_workspace": False})

    tool = ExecTool.create(ToolContext.from_settings(settings))
    result = await tool.execute(command=_cwd_command(), workdir=str(outside))

    assert str(outside.resolve()) in result
    assert "outside the configured workspace" not in result


def test_exec_tool_create_reads_security_exec_sandbox_setting(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    settings = Settings(
        _env_file=None,
        home_dir=home,
        security={"restrict_to_workspace": True, "exec_sandbox": "bwrap"},
    )

    tool = ExecTool.create(ToolContext.from_settings(settings))

    assert tool.sandbox == "bwrap"
