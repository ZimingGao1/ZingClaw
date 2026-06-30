from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from zingclaw.agent.subagent import SubagentManager
from zingclaw.config import Settings
from zingclaw.providers import LLMProvider, LLMResponse
from zingclaw.session import SessionManager
from zingclaw.tools.spawn import CancelSubagentTool, ListSubagentsTool, SpawnTool
from zingclaw.tools import RequestContext


class RecordingProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse], *, delay: float = 0.0):
        super().__init__(api_key=None, api_base=None)
        self.responses = list(responses)
        self.delay = delay
        self.requests: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_text_delta=None,
    ) -> LLMResponse:
        del tools, model, max_tokens, temperature, reasoning_effort, tool_choice, on_text_delta
        self.requests.append(deepcopy(messages))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.responses.pop(0)

    def get_default_model(self) -> str:
        return "dummy-model"


@pytest.mark.asyncio
async def test_spawn_tool_runs_background_subagent_and_persists_result(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    manager = SubagentManager(
        settings,
        sessions=SessionManager(tmp_path / "sessions"),
        provider_factory=lambda: RecordingProvider([LLMResponse(content="subagent done", finish_reason="stop")]),
    )
    tool = SpawnTool(manager)
    token = tool.set_context(RequestContext(session_key="cli:demo", channel="cli", chat_id="demo"))

    try:
        started = await tool.execute("Inspect the workspace and summarize what matters.")
    finally:
        tool.reset_context(token)

    assert "started" in started
    assert manager.wait_for_all(timeout=3.0) is True

    notices = manager.drain_notifications("cli:demo")
    session = manager.sessions.get_or_create("cli:demo")

    assert len(notices) == 1
    assert "[Background Task" in notices[0]
    assert "subagent done" in notices[0]
    assert session.messages[-1]["injected_event"] == "subagent_result"
    assert "subagent done" in session.messages[-1]["content"]


@pytest.mark.asyncio
async def test_spawn_tool_enforces_concurrency_limit(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path, max_concurrent_subagents=1)
    manager = SubagentManager(
        settings,
        sessions=SessionManager(tmp_path / "sessions"),
        provider_factory=lambda: RecordingProvider(
            [LLMResponse(content="later", finish_reason="stop")],
            delay=0.4,
        ),
        max_concurrent_subagents=1,
    )
    tool = SpawnTool(manager)
    token = tool.set_context(RequestContext(session_key="cli:demo", channel="cli", chat_id="demo"))

    try:
        first = await tool.execute("First background task")
        second = await tool.execute("Second background task")
    finally:
        tool.reset_context(token)

    assert "started" in first
    assert "concurrency limit reached" in second
    assert manager.wait_for_all(timeout=3.0) is True


@pytest.mark.asyncio
async def test_list_subagents_reports_running_and_finished_tasks(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    manager = SubagentManager(
        settings,
        sessions=SessionManager(tmp_path / "sessions"),
        provider_factory=lambda: RecordingProvider(
            [LLMResponse(content="done", finish_reason="stop")],
            delay=0.2,
        ),
    )
    spawn_tool = SpawnTool(manager)
    list_tool = ListSubagentsTool(manager)
    token = spawn_tool.set_context(RequestContext(session_key="cli:demo", channel="cli", chat_id="demo"))
    list_token = list_tool.set_context(RequestContext(session_key="cli:demo", channel="cli", chat_id="demo"))

    try:
        started = await spawn_tool.execute("Background listing target")
        running_snapshot = await list_tool.execute(running_only=True)
        assert "started" in started
        assert running_snapshot["count"] == 1
        assert running_snapshot["subagents"][0]["running"] is True
        assert running_snapshot["subagents"][0]["session_key"] == "cli:demo"

        assert manager.wait_for_all(timeout=3.0) is True
        finished_snapshot = await list_tool.execute()
    finally:
        spawn_tool.reset_context(token)
        list_tool.reset_context(list_token)

    assert finished_snapshot["count"] == 1
    assert finished_snapshot["subagents"][0]["running"] is False
    assert finished_snapshot["subagents"][0]["stop_reason"] == "completed"
    assert finished_snapshot["subagents"][0]["result_preview"] == "done"


@pytest.mark.asyncio
async def test_cancel_subagent_stops_running_task_and_records_cancelled_result(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    manager = SubagentManager(
        settings,
        sessions=SessionManager(tmp_path / "sessions"),
        provider_factory=lambda: RecordingProvider(
            [LLMResponse(content="too late", finish_reason="stop")],
            delay=2.0,
        ),
    )
    spawn_tool = SpawnTool(manager)
    list_tool = ListSubagentsTool(manager)
    cancel_tool = CancelSubagentTool(manager)
    ctx = RequestContext(session_key="cli:demo", channel="cli", chat_id="demo")
    spawn_token = spawn_tool.set_context(ctx)
    list_token = list_tool.set_context(ctx)
    cancel_token = cancel_tool.set_context(ctx)

    try:
        started = await spawn_tool.execute("Long running background task")
        snapshot = await list_tool.execute(running_only=True)
        task_id = snapshot["subagents"][0]["task_id"]
        cancelled = await cancel_tool.execute(task_id)
        assert "started" in started
        assert "Cancelled subagent" in cancelled or "Cancellation requested" in cancelled
        assert manager.wait_for_all(timeout=3.0) is True
        final_snapshot = await list_tool.execute()
    finally:
        spawn_tool.reset_context(spawn_token)
        list_tool.reset_context(list_token)
        cancel_tool.reset_context(cancel_token)

    notices = manager.drain_notifications("cli:demo")
    session = manager.sessions.get_or_create("cli:demo")

    assert final_snapshot["subagents"][0]["stop_reason"] == "cancelled"
    assert final_snapshot["subagents"][0]["cancel_requested"] is True
    assert any("cancelled" in notice.lower() for notice in notices)
    assert session.messages[-1]["injected_event"] == "subagent_result"
    assert "cancelled" in session.messages[-1]["content"].lower()


@pytest.mark.asyncio
async def test_spawn_tool_requires_active_session_context(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    manager = SubagentManager(
        settings,
        sessions=SessionManager(tmp_path / "sessions"),
        provider_factory=lambda: RecordingProvider([LLMResponse(content="done", finish_reason="stop")]),
    )
    tool = SpawnTool(manager)

    result = await tool.execute("No session")

    assert result == "Error: spawn requires an active session context."
