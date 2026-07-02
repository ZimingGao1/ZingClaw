from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from zingclaw.tools import ExecTool, ListExecSessionsTool, RequestContext, WriteStdinTool
from zingclaw.tools.exec_session import ExecSessionManager


def _python_command(code: str) -> str:
    if sys.platform == "win32":
        return f"{subprocess.list2cmdline([sys.executable])} -u -c {subprocess.list2cmdline([code])}"
    return f"{shlex.quote(sys.executable)} -u -c {shlex.quote(code)}"


def _session_id(output: str) -> str:
    match = re.search(r"session_id:\s*([0-9a-f]+)", output)
    assert match, output
    return match.group(1)


@pytest.mark.asyncio
async def test_exec_keeps_one_shot_behavior_without_yield_time_ms(tmp_path: Path) -> None:
    tool = ExecTool(working_dir=str(tmp_path), timeout=5)

    result = await tool.execute(command="echo hello")

    assert "hello" in result
    assert "Exit code: 0" in result
    assert "session_id:" not in result


@pytest.mark.asyncio
async def test_exec_accepts_command_aliases_and_max_output_tokens_for_one_shot(tmp_path: Path) -> None:
    tool = ExecTool(working_dir="/", timeout=5)
    command = _python_command("import os; print(os.getcwd()); print('A' * 2000)")

    result = await tool.execute(cmd=command, workdir=str(tmp_path), max_output_tokens=1000)

    assert str(tmp_path) in result
    assert "chars truncated" in result
    assert "Exit code: 0" in result


@pytest.mark.asyncio
async def test_exec_session_can_continue_with_write_stdin(tmp_path: Path) -> None:
    manager = ExecSessionManager()
    exec_tool = ExecTool(working_dir=str(tmp_path), timeout=5, session_manager=manager)
    stdin_tool = WriteStdinTool(manager=manager)
    command = _python_command(
        "import sys; print('ready', flush=True); "
        "line=sys.stdin.readline(); print('got:' + line.strip(), flush=True)"
    )

    initial = await exec_tool.execute(command=command, yield_time_ms=500)
    sid = _session_id(initial)
    result = await stdin_tool.execute(session_id=sid, chars="ping\n", yield_time_ms=1000)

    assert "ready" in initial + result
    assert "Process running" in initial
    assert "got:ping" in result
    assert "Exit code: 0" in result
    assert "Elapsed:" in result


@pytest.mark.asyncio
async def test_write_stdin_can_close_stdin_and_wait_for_output(tmp_path: Path) -> None:
    manager = ExecSessionManager()
    exec_tool = ExecTool(working_dir=str(tmp_path), timeout=5, session_manager=manager)
    stdin_tool = WriteStdinTool(manager=manager)
    command = _python_command(
        "import sys; print('ready', flush=True); "
        "data=sys.stdin.read(); print('got:' + data, flush=True)"
    )

    initial = await exec_tool.execute(command=command, yield_time_ms=300)
    sid = _session_id(initial)
    result = await stdin_tool.execute(
        session_id=sid,
        chars="payload",
        close_stdin=True,
        wait_for="got:payload",
        wait_timeout_ms=3000,
        yield_time_ms=0,
    )

    assert "ready" in initial + result
    assert "got:payload" in result
    assert "Stdin closed." in result
    assert "Exit code: 0" in result
    assert "Wait target not observed" not in result


@pytest.mark.asyncio
async def test_write_stdin_can_terminate_and_report_timeout_without_killing_session(tmp_path: Path) -> None:
    manager = ExecSessionManager()
    exec_tool = ExecTool(working_dir=str(tmp_path), timeout=30, session_manager=manager)
    stdin_tool = WriteStdinTool(manager=manager)
    command = _python_command("import time; print('booting', flush=True); time.sleep(5)")

    initial = await exec_tool.execute(command=command, yield_time_ms=100)
    sid = _session_id(initial)
    waited = await stdin_tool.execute(
        session_id=sid,
        wait_for="never-ready",
        wait_timeout_ms=200,
        yield_time_ms=0,
    )
    cleanup = await stdin_tool.execute(session_id=sid, terminate=True, yield_time_ms=0)

    assert "Process running" in initial
    assert "booting" in initial + waited
    assert "Wait target not observed: 'never-ready'" in waited
    assert "Process running" in waited
    assert "Session terminated." in cleanup
    assert "Exit code:" in cleanup


@pytest.mark.asyncio
async def test_exec_session_accepts_max_output_tokens_alias_and_list_tool_reports_sessions(tmp_path: Path) -> None:
    manager = ExecSessionManager()
    exec_tool = ExecTool(working_dir=str(tmp_path), timeout=5, session_manager=manager)
    stdin_tool = WriteStdinTool(manager=manager)
    list_tool = ListExecSessionsTool(manager=manager)
    command = _python_command("import time; print('A' * 2000, flush=True); time.sleep(5)")

    initial = await exec_tool.execute(command=command, yield_time_ms=0)
    sid = _session_id(initial)
    poll = await stdin_tool.execute(session_id=sid, yield_time_ms=500, max_output_tokens=1000)
    listing = await list_tool.execute()
    cleanup = await stdin_tool.execute(session_id=sid, terminate=True, yield_time_ms=0)

    assert "Process running" in initial
    assert "chars truncated" in poll
    assert sid in listing
    assert "running" in listing
    assert "elapsed=" in listing
    assert "remaining=" in listing
    assert str(tmp_path) in listing
    assert "Session terminated." in cleanup


@pytest.mark.asyncio
async def test_exec_session_tools_respect_request_session_key_isolation(tmp_path: Path) -> None:
    manager = ExecSessionManager()
    exec_tool = ExecTool(working_dir=str(tmp_path), timeout=5, session_manager=manager)
    list_tool = ListExecSessionsTool(manager=manager)
    stdin_tool = WriteStdinTool(manager=manager)
    command = _python_command("import time; print('ready', flush=True); time.sleep(5)")

    exec_token = exec_tool.set_context(RequestContext(session_key="cli:a"))
    try:
        initial = await exec_tool.execute(command=command, yield_time_ms=100)
    finally:
        exec_tool.reset_context(exec_token)

    sid = _session_id(initial)
    list_a_token = list_tool.set_context(RequestContext(session_key="cli:a"))
    try:
        listing_a = await list_tool.execute()
    finally:
        list_tool.reset_context(list_a_token)

    list_b_token = list_tool.set_context(RequestContext(session_key="cli:b"))
    try:
        listing_b = await list_tool.execute()
    finally:
        list_tool.reset_context(list_b_token)

    cleanup_token = stdin_tool.set_context(RequestContext(session_key="cli:a"))
    try:
        cleanup = await stdin_tool.execute(session_id=sid, terminate=True, yield_time_ms=0)
    finally:
        stdin_tool.reset_context(cleanup_token)

    assert sid in listing_a
    assert listing_b == "No active exec sessions."
    assert "Session terminated." in cleanup


def test_write_stdin_reports_missing_session() -> None:
    tool = WriteStdinTool(manager=ExecSessionManager())

    result = asyncio.run(tool.execute(session_id="missing", chars=""))

    assert "exec session not found" in result


def test_write_stdin_reports_blank_session_id() -> None:
    tool = WriteStdinTool(manager=ExecSessionManager())

    result = asyncio.run(tool.execute(session_id="   ", chars=""))

    assert result == "Error: session_id is required"
