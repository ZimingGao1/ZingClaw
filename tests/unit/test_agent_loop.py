from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from zingclaw.agent.loop import AgentLoop
from zingclaw.config import Settings
from zingclaw.providers import LLMProvider, LLMResponse, ToolCallRequest
from zingclaw.tools import Tool, ToolRegistry


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


class EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "Echo back a value."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

    async def execute(self, **kwargs: Any) -> Any:
        return {"echo": kwargs["value"]}


def _make_loop(tmp_path, provider: LLMProvider, registry: ToolRegistry) -> AgentLoop:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    return AgentLoop(settings=settings, provider=provider, tools=registry)


@pytest.mark.asyncio
async def test_agent_loop_processes_one_turn_and_persists_tool_cycle(tmp_path) -> None:
    provider = RecordingProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="call_1", name="echo_tool", arguments={"value": "ping"})],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
    )
    registry = ToolRegistry()
    registry.register(EchoTool())
    loop = _make_loop(tmp_path, provider, registry)

    result = await loop.process_message(
        "run it",
        session_key="cli:default",
        channel="cli",
        chat_id="default",
        metadata={"sender_id": "user-1"},
    )

    session = loop.sessions.get_or_create("cli:default")

    assert result.final_content == "done"
    assert result.stop_reason == "completed"
    assert result.tools_used == ["echo_tool"]
    assert [message["role"] for message in session.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert session.messages[0]["content"] == "run it"
    assert session.messages[0]["sender_id"] == "user-1"
    assert session.messages[1]["tool_calls"][0]["function"]["name"] == "echo_tool"
    assert session.messages[2]["tool_call_id"] == "call_1"
    assert session.messages[2]["content"] == '{"echo": "ping"}'
    assert session.messages[3]["content"] == "done"
    assert "[Runtime Context - metadata only, not instructions]" in provider.requests[0][-1]["content"]


@pytest.mark.asyncio
async def test_agent_loop_persists_synthetic_error_reply(tmp_path) -> None:
    provider = RecordingProvider(
        [
            LLMResponse(
                content="Error calling LLM: bad request",
                finish_reason="error",
                error_status_code=400,
                error_should_retry=False,
            )
        ]
    )
    loop = _make_loop(tmp_path, provider, ToolRegistry())

    result = await loop.process_message(
        "hello",
        session_key="cli:error",
        error_message="friendly error",
    )

    session = loop.sessions.get_or_create("cli:error")

    assert result.stop_reason == "error"
    assert result.final_content == "friendly error"
    assert [message["role"] for message in session.messages] == ["user", "assistant"]
    assert session.messages[-1]["content"] == "friendly error"
