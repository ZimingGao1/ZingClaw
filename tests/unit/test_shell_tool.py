from __future__ import annotations

import sys
from pathlib import Path

import pytest

from zingclaw.tools import ExecTool


def _echo_hello_command() -> str:
    return "echo hello"


def _cwd_command() -> str:
    return "cd" if sys.platform == "win32" else "pwd"


def _sleep_command() -> str:
    return "ping -n 3 127.0.0.1 >nul" if sys.platform == "win32" else "sleep 2"


def _large_output_command() -> str:
    if sys.platform == "win32":
        return 'powershell -NoProfile -Command "$s=\'A\' * 4000; Write-Output $s"'
    return "yes A | head -c 4000"


def _env_echo_command(name: str) -> str:
    if sys.platform == "win32":
        return f"echo %{name}%"
    return f'printf "%s" "${name}"'


@pytest.mark.asyncio
async def test_exec_runs_command_and_returns_exit_code(tmp_path: Path) -> None:
    tool = ExecTool(working_dir=str(tmp_path), restrict_to_workspace=True, timeout=5)

    result = await tool.execute(command=_echo_hello_command())

    assert "hello" in result
    assert "Exit code: 0" in result


@pytest.mark.asyncio
async def test_exec_uses_workspace_as_default_cwd(tmp_path: Path) -> None:
    tool = ExecTool(working_dir=str(tmp_path), restrict_to_workspace=True, timeout=5)
    command = _cwd_command()

    result = await tool.execute(command=command)

    assert str(tmp_path.resolve()) in result


@pytest.mark.asyncio
async def test_exec_blocks_workdir_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True, timeout=5)

    result = await tool.execute(
        command=_echo_hello_command(),
        workdir=str(outside),
    )

    assert "outside the configured workspace" in result
    assert "hard policy boundary" in result


def test_guard_command_blocks_absolute_path_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.txt"

    tool = ExecTool(working_dir=str(workspace), restrict_to_workspace=True)
    command = f"type {target}" if sys.platform == "win32" else f"cat {target}"

    error = tool._guard_command(
        command,
        str(workspace),
        restrict_to_workspace=True,
        workspace_root=str(workspace),
    )

    assert error is not None
    assert "path outside working dir" in error


@pytest.mark.asyncio
async def test_exec_times_out_long_running_command(tmp_path: Path) -> None:
    tool = ExecTool(working_dir=str(tmp_path), restrict_to_workspace=True, timeout=1)
    command = _sleep_command()

    result = await tool.execute(command=command)

    assert "timed out" in result
    assert "1 seconds" in result


@pytest.mark.asyncio
async def test_exec_truncates_large_output(tmp_path: Path) -> None:
    tool = ExecTool(
        working_dir=str(tmp_path),
        restrict_to_workspace=True,
        timeout=5,
        max_output_chars=2000,
    )
    command = _large_output_command()

    result = await tool.execute(command=command)

    assert "chars truncated" in result
    assert "Exit code: 0" in result


@pytest.mark.asyncio
async def test_exec_allows_whitelisted_env_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_CUSTOM_VAR", "allowed-value")
    tool = ExecTool(
        working_dir=str(tmp_path),
        restrict_to_workspace=True,
        timeout=5,
        allowed_env_keys=["MY_CUSTOM_VAR"],
    )
    command = _env_echo_command("MY_CUSTOM_VAR")

    result = await tool.execute(command=command)

    assert "allowed-value" in result


def test_prepare_command_wraps_with_configured_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = ExecTool(working_dir=str(tmp_path), restrict_to_workspace=True, sandbox="bwrap")

    monkeypatch.setattr("zingclaw.tools.shell._IS_WINDOWS", False)
    monkeypatch.setattr("zingclaw.tools.shell.sandbox_backend_available", lambda name: name == "bwrap")
    monkeypatch.setattr(
        "zingclaw.tools.shell.wrap_command",
        lambda sandbox, command, workspace, cwd: f"wrapped::{sandbox}::{command}::{workspace}::{cwd}",
    )

    prepared = tool._prepare_command("echo hello")

    assert not isinstance(prepared, str)
    assert prepared.command == (
        f"wrapped::bwrap::echo hello::{tmp_path.resolve()}::{tmp_path.resolve()}"
    )
    assert prepared.cwd == str(tmp_path.resolve())


def test_prepare_command_errors_when_sandbox_backend_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = ExecTool(working_dir=str(tmp_path), restrict_to_workspace=True, sandbox="bwrap")

    monkeypatch.setattr("zingclaw.tools.shell._IS_WINDOWS", False)
    monkeypatch.setattr("zingclaw.tools.shell.sandbox_backend_available", lambda _name: False)

    prepared = tool._prepare_command("echo hello")

    assert isinstance(prepared, str)
    assert "sandbox backend 'bwrap' is not available" in prepared


def test_prepare_command_errors_when_sandbox_is_configured_on_windows(tmp_path: Path) -> None:
    tool = ExecTool(working_dir=str(tmp_path), restrict_to_workspace=True, sandbox="bwrap")

    prepared = tool._prepare_command("echo hello")

    assert isinstance(prepared, str)
    assert "sandbox backend 'bwrap' is not supported on Windows" in prepared
