from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from zingclaw.agent.loop import AgentLoop
from zingclaw.config import Settings
from zingclaw.providers import LLMProvider, LLMResponse, ToolCallRequest
from zingclaw.session import GOAL_STATE_KEY, SessionManager
from zingclaw.tools import CompleteGoalTool, LongTaskTool, RequestContext


class RecordingProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]):
        super().__init__(api_key=None, api_base=None)
        self.responses = list(responses)
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
        return self.responses.pop(0)

    def get_default_model(self) -> str:
        return "dummy-model"


@pytest.mark.asyncio
async def test_goal_tools_store_and_complete_session_goal_state(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path / "sessions")
    sessions.save(sessions.get_or_create("cli:demo"))

    start_tool = LongTaskTool(sessions=sessions)
    start_token = start_tool.set_context(RequestContext(session_key="cli:demo", channel="cli", chat_id="demo"))
    try:
        started = await start_tool.execute(
            goal="Finish the roadmap rewrite and verify the updated CLI flow.",
            ui_summary="Roadmap rewrite",
        )
    finally:
        start_tool.reset_context(start_token)

    active = sessions.get_or_create("cli:demo").metadata[GOAL_STATE_KEY]
    assert "Goal recorded." in started
    assert active["status"] == "active"
    assert active["objective"] == "Finish the roadmap rewrite and verify the updated CLI flow."
    assert active["ui_summary"] == "Roadmap rewrite"
    assert "started_at" in active

    complete_tool = CompleteGoalTool(sessions=sessions)
    complete_token = complete_tool.set_context(RequestContext(session_key="cli:demo"))
    try:
        completed = await complete_tool.execute("Roadmap updated and CLI flow rechecked.")
    finally:
        complete_tool.reset_context(complete_token)

    finished = sessions.get_or_create("cli:demo").metadata[GOAL_STATE_KEY]
    assert "Goal marked complete" in completed
    assert finished["status"] == "completed"
    assert finished["recap"] == "Roadmap updated and CLI flow rechecked."
    assert "completed_at" in finished


@pytest.mark.asyncio
async def test_agent_loop_long_task_persists_goal_and_reinjects_it_next_turn(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    provider = RecordingProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call_goal",
                        name="long_task",
                        arguments={
                            "goal": "Finish the provider registry migration and verify the relevant tests.",
                            "ui_summary": "Provider registry",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="Goal tracking is now active.", finish_reason="stop"),
            LLMResponse(content="Continuing work under the same goal.", finish_reason="stop"),
        ]
    )
    loop = AgentLoop(settings=settings, provider=provider)

    first = await loop.process_message(
        "Please keep working on the provider registry migration.",
        session_key="cli:goal",
        channel="cli",
        chat_id="goal",
    )
    second = await loop.process_message(
        "continue",
        session_key="cli:goal",
        channel="cli",
        chat_id="goal",
    )

    session = loop.sessions.get_or_create("cli:goal")
    goal_state = session.metadata[GOAL_STATE_KEY]
    second_request_user_message = provider.requests[-1][-1]["content"]

    assert first.final_content == "Goal tracking is now active."
    assert second.final_content == "Continuing work under the same goal."
    assert goal_state["status"] == "active"
    assert goal_state["ui_summary"] == "Provider registry"
    assert "Goal (active):" in second_request_user_message
    assert "Finish the provider registry migration and verify the relevant tests." in (
        second_request_user_message
    )
    assert "Summary: Provider registry" in second_request_user_message


@pytest.mark.asyncio
async def test_complete_goal_reports_when_no_active_goal(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path / "sessions")
    sessions.save(sessions.get_or_create("cli:demo"))
    tool = CompleteGoalTool(sessions=sessions)
    token = tool.set_context(RequestContext(session_key="cli:demo"))

    try:
        result = await tool.execute("nothing to do")
    finally:
        tool.reset_context(token)

    assert result == "No active goal to complete."
