from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from zingclaw.agent.loop import AgentLoop
from zingclaw.bus import InboundMessage, MessageBus
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
        del tools, model, max_tokens, temperature, reasoning_effort, tool_choice
        self.requests.append(deepcopy(messages))
        response = self.responses.pop(0)
        if on_text_delta is not None and response.content:
            await on_text_delta(response.content)
        return response

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


@pytest.mark.asyncio
async def test_agent_loop_run_streams_bus_deltas_and_stream_end_marker(tmp_path) -> None:
    provider = RecordingProvider(
        [
            LLMResponse(content="streamed answer", finish_reason="stop")
        ]
    )
    registry = ToolRegistry()
    loop = _make_loop(tmp_path, provider, registry)
    bus = MessageBus()
    loop.bind_bus(bus)
    task = asyncio.create_task(loop.run())

    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="cli",
                chat_id="demo",
                session_key="cli:demo",
                sender_id="user-1",
                text="hello",
                metadata={"_wants_stream": True},
            )
        )

        delta = await bus.consume_outbound()
        final = await bus.consume_outbound()
    finally:
        loop.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert delta.text == "streamed answer"
    assert delta.final is False
    assert delta.metadata == {"_stream_delta": True}
    assert final.text == ""
    assert final.final is True
    assert final.metadata["_stream_end"] is True
    assert final.metadata["_streamed"] is True
    assert final.metadata["stop_reason"] == "completed"
    assert final.metadata["tools_used"] == []
    assert final.metadata["usage"] == {}


@pytest.mark.asyncio
async def test_agent_loop_run_publishes_single_final_outbound_without_streaming(tmp_path) -> None:
    provider = RecordingProvider(
        [
            LLMResponse(content="plain answer", finish_reason="stop")
        ]
    )
    loop = _make_loop(tmp_path, provider, ToolRegistry())
    bus = MessageBus()
    loop.bind_bus(bus)
    task = asyncio.create_task(loop.run())

    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="cli",
                chat_id="demo",
                session_key="cli:demo",
                text="hello",
            )
        )

        outbound = await bus.consume_outbound()
    finally:
        loop.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert outbound.text == "plain answer"
    assert outbound.final is True
    assert outbound.metadata["stop_reason"] == "completed"
    assert "_stream_end" not in outbound.metadata
