from __future__ import annotations

from typing import Any

import pytest

from zingclaw.agent.loop import AgentLoop
from zingclaw.agent.runner import AgentRunSpec, AgentRunner
from zingclaw.bus import (
    RuntimeEventBus,
    RuntimeEventHook,
    SessionTurnStarted,
    TurnRunStatusChanged,
    build_runtime_event_context,
)
from zingclaw.config import Settings
from zingclaw.providers import LLMProvider, LLMResponse, ToolCallRequest
from zingclaw.tools import Tool, ToolRegistry


class RecordingProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]):
        super().__init__(api_key=None, api_base=None)
        self.responses = list(responses)

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
        del messages, tools, model, max_tokens, temperature, reasoning_effort, tool_choice
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


def _event_view(event: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"type": type(event).__name__}
    if hasattr(event, "status"):
        row["status"] = event.status
    if hasattr(event, "tool_name"):
        row["tool_name"] = event.tool_name
    if hasattr(event, "delta"):
        row["delta"] = event.delta
    if hasattr(event, "stop_reason"):
        row["stop_reason"] = event.stop_reason
    if hasattr(event, "result_preview"):
        row["result_preview"] = event.result_preview
    row["session_key"] = event.context.session_key
    row["channel"] = event.context.channel
    row["chat_id"] = event.context.chat_id
    return row


@pytest.mark.asyncio
async def test_runtime_event_bus_subscribe_filters_by_type() -> None:
    bus = RuntimeEventBus()
    seen: list[str] = []

    async def _on_status(event: TurnRunStatusChanged) -> None:
        seen.append(f"status:{event.status}")

    bus.subscribe(_on_status, TurnRunStatusChanged)
    await bus.publish(
        TurnRunStatusChanged(
            context=build_runtime_event_context(channel="cli", chat_id="demo", session_key="s1"),
            status="running",
        )
    )
    await bus.publish(
        SessionTurnStarted(
            context=build_runtime_event_context(channel="cli", chat_id="demo", session_key="s1")
        )
    )

    assert seen == ["status:running"]


@pytest.mark.asyncio
async def test_agent_runner_emits_runtime_events_for_tool_cycle() -> None:
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
    runtime_bus = RuntimeEventBus()
    events: list[dict[str, Any]] = []
    runtime_bus.subscribe(lambda event: events.append(_event_view(event)))
    runner = AgentRunner(provider)

    result = await runner.run(
        AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "use tool"},
            ],
            tools=registry,
            model="dummy-model",
            max_iterations=3,
            max_tool_result_chars=1000,
            session_key="cli:demo",
            channel="cli",
            chat_id="demo",
            metadata={"sender_id": "user-1"},
            hooks=[
                RuntimeEventHook(
                    runtime_bus,
                    channel="cli",
                    chat_id="demo",
                    session_key="cli:demo",
                    metadata={"sender_id": "user-1"},
                )
            ],
        )
    )

    assert result.final_content == "done"
    assert [row["type"] for row in events] == [
        "TurnRunStatusChanged",
        "TurnRunStatusChanged",
        "TurnRunStatusChanged",
        "ToolCallStarted",
        "ToolCallCompleted",
        "TurnRunStatusChanged",
        "TurnOutputDelta",
        "TurnRunStatusChanged",
    ]
    assert events[0]["status"] == "running"
    assert events[1]["status"] == "calling_model"
    assert events[2]["status"] == "executing_tools"
    assert events[3]["tool_name"] == "echo_tool"
    assert events[4]["result_preview"] == '{"echo": "ping"}'
    assert events[5]["status"] == "calling_model"
    assert events[6]["delta"] == "done"
    assert events[7]["status"] == "completed"
    assert all(row["session_key"] == "cli:demo" for row in events)


@pytest.mark.asyncio
async def test_agent_loop_publishes_turn_scoped_runtime_events(tmp_path) -> None:
    provider = RecordingProvider([LLMResponse(content="streamed answer", finish_reason="stop")])
    runtime_bus = RuntimeEventBus()
    events: list[dict[str, Any]] = []
    runtime_bus.subscribe(lambda event: events.append(_event_view(event)))
    loop = AgentLoop(
        settings=Settings(_env_file=None, home_dir=tmp_path),
        provider=provider,
        tools=ToolRegistry(),
        runtime_events=runtime_bus,
    )

    result = await loop.process_message(
        "hello",
        session_key="cli:demo",
        channel="cli",
        chat_id="demo",
        metadata={"sender_id": "user-1"},
    )

    assert result.final_content == "streamed answer"
    assert [row["type"] for row in events] == [
        "SessionTurnStarted",
        "TurnRunStatusChanged",
        "TurnRunStatusChanged",
        "TurnRunStatusChanged",
        "TurnOutputDelta",
        "TurnRunStatusChanged",
        "TurnCompleted",
    ]
    assert events[0]["session_key"] == "cli:demo"
    assert events[1]["status"] == "building_context"
    assert events[2]["status"] == "running"
    assert events[3]["status"] == "calling_model"
    assert events[4]["delta"] == "streamed answer"
    assert events[5]["status"] == "completed"
    assert events[6]["stop_reason"] == "completed"
    assert events[6]["session_key"] == "cli:demo"
