from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from zingclaw.agent.loop import AgentLoop
from zingclaw.config import Settings
from zingclaw.providers import LLMProvider, LLMResponse, ToolCallRequest
from zingclaw.tools import RequestContext, SaveMemoryTool


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
async def test_save_memory_tool_appends_to_memory_file(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    tool = SaveMemoryTool.create(type("Ctx", (), {"workspace": settings.workspace_dir, "settings": settings})())

    token = tool.set_context(RequestContext(session_key="cli:demo"))
    try:
        result = await tool.execute("The user prefers concise Chinese replies.")
    finally:
        tool.reset_context(token)

    memory_text = (settings.memory_dir / "MEMORY.md").read_text(encoding="utf-8")

    assert result == "Saved to long-term memory for session cli:demo."
    assert "The user prefers concise Chinese replies." in memory_text
    assert "## " in memory_text


@pytest.mark.asyncio
async def test_agent_loop_save_memory_tool_call_persists_memory(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    provider = RecordingProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call_memory",
                        name="save_memory",
                        arguments={"content": "Project constraint: keep all MCP tools behind the unified registry."},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="I saved that for future turns.", finish_reason="stop"),
        ]
    )
    loop = AgentLoop(settings=settings, provider=provider)

    result = await loop.process_message(
        "Please remember our MCP registry constraint.",
        session_key="cli:memory",
        channel="cli",
        chat_id="memory",
    )

    session = loop.sessions.get_or_create("cli:memory")
    memory_text = (settings.memory_dir / "MEMORY.md").read_text(encoding="utf-8")

    assert result.final_content == "I saved that for future turns."
    assert session.messages[1]["tool_calls"][0]["function"]["name"] == "save_memory"
    assert session.messages[2]["name"] == "save_memory"
    assert "Saved to long-term memory for session cli:memory." in session.messages[2]["content"]
    assert "Project constraint: keep all MCP tools behind the unified registry." in memory_text
