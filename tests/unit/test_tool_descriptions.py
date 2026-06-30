from __future__ import annotations

from zingclaw.tools import (
    ApplyPatchTool,
    CancelSubagentTool,
    CronTool,
    EditFileTool,
    ExecTool,
    FindFilesTool,
    GrepTool,
    ListSubagentsTool,
    ListExecSessionsTool,
    MessageTool,
    ReadFileTool,
    SpawnTool,
    WebSearchTool,
    WriteFileTool,
    WriteStdinTool,
)


class _DummySubagentManager:
    async def spawn(self, **kwargs):
        return str(kwargs)

    def list_statuses(self, **kwargs):
        return [kwargs]

    async def cancel_task(self, *args, **kwargs):
        return str((args, kwargs))


class _DummyCronService:
    def list_jobs(self):
        return []


def test_coding_tool_descriptions_steer_editing_priority() -> None:
    apply_patch = ApplyPatchTool().description.lower()
    edit_file = EditFileTool().description.lower()
    write_file = WriteFileTool().description.lower()

    assert "default tool for code edits" in apply_patch
    assert "multi-file" in apply_patch
    assert "dry_run=true" in apply_patch
    assert "edit_file only for small exact replacements" in apply_patch

    assert "small exact replacement" in edit_file
    assert "copied from read_file" in edit_file
    assert "prefer apply_patch" in edit_file

    assert "replace an entire utf-8 text file" in write_file
    assert "prefer apply_patch" in write_file


def test_coding_tool_descriptions_steer_discovery_and_shell_usage() -> None:
    read_file = ReadFileTool().description.lower()
    find_files = FindFilesTool().description.lower()
    grep = GrepTool().description.lower()
    exec_tool = ExecTool().description.lower()
    write_stdin = WriteStdinTool().description.lower()
    list_sessions = ListExecSessionsTool().description.lower()
    web_search = WebSearchTool().description.lower()
    message = MessageTool().description.lower()
    spawn = SpawnTool(_DummySubagentManager()).description.lower()
    list_subagents = ListSubagentsTool(_DummySubagentManager()).description.lower()
    cancel_subagent = CancelSubagentTool(_DummySubagentManager()).description.lower()
    cron = CronTool(_DummyCronService()).description.lower()

    assert "find_files/list_dir first" in read_file
    assert "before editing" in read_file
    assert "prefer it over shell find/ls" in find_files
    assert "prefer this over shell grep" in grep

    assert "tests, builds" in exec_tool
    assert "prefer read_file/find_files/grep" in exec_tool
    assert "apply_patch/write_file/edit_file" in exec_tool
    assert "yield_time_ms" in exec_tool
    assert "do not use this to start new commands" in write_stdin
    assert "wait_for" in write_stdin
    assert "recover a session_id" in list_sessions
    assert "discover pages first" in web_search
    assert "web_fetch" in web_search
    assert "proactively send a message" in message
    assert "carry media" in message
    assert "background subagent" in spawn
    assert "same session" in spawn
    assert "list background subagents" in list_subagents
    assert "current phase" in list_subagents
    assert "cancel a running background subagent" in cancel_subagent
    assert "list_subagents first" in cancel_subagent
    assert "schedule reminders and recurring tasks" in cron
    assert "actions: add, list, remove" in cron
