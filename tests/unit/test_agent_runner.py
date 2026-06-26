from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from zingclaw.agent.runner import AgentRunSpec, AgentRunner
from zingclaw.providers import LLMProvider, LLMResponse, ToolCallRequest
from zingclaw.tools import Tool, ToolRegistry


class RecordingProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]):
        super().__init__(api_key=None, api_base=None)
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

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
        self.requests.append(
            {
                "messages": deepcopy(messages),
                "tools": deepcopy(tools),
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "reasoning_effort": reasoning_effort,
                "tool_choice": deepcopy(tool_choice),
                "on_text_delta": on_text_delta,
            }
        )
        if on_text_delta is not None and self.responses and self.responses[0].content:
            await on_text_delta(self.responses[0].content)
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


@pytest.mark.asyncio
async def test_runner_returns_final_response_without_tool_calls() -> None:
    provider = RecordingProvider(
        [
            LLMResponse(
                content="done",
                finish_reason="stop",
                usage={"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
            )
        ]
    )
    runner = AgentRunner(provider)
    registry = ToolRegistry()
    spec = AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ],
        tools=registry,
        model="dummy-model",
        max_iterations=3,
        max_tool_result_chars=1000,
    )

    result = await runner.run(spec)

    assert result.final_content == "done"
    assert result.stop_reason == "completed"
    assert result.error is None
    assert result.tools_used == []
    assert result.usage["total_tokens"] == 15
    assert result.messages[-1] == {"role": "assistant", "content": "done"}
    assert provider.requests[0]["tools"] is None


@pytest.mark.asyncio
async def test_runner_executes_tool_calls_and_replays_tool_messages() -> None:
    provider = RecordingProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(id="call_1", name="echo_tool", arguments={"value": "ping"})
                ],
                finish_reason="tool_calls",
                usage={"total_tokens": 10},
            ),
            LLMResponse(
                content="all done",
                finish_reason="stop",
                usage={"total_tokens": 7},
            ),
        ]
    )
    runner = AgentRunner(provider)
    registry = ToolRegistry()
    registry.register(EchoTool())
    spec = AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "use a tool"},
        ],
        tools=registry,
        model="dummy-model",
        max_iterations=3,
        max_tool_result_chars=1000,
    )

    result = await runner.run(spec)

    assert result.final_content == "all done"
    assert result.tools_used == ["echo_tool"]
    assert result.usage["total_tokens"] == 17
    assert [message["role"] for message in result.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert result.messages[2]["tool_calls"][0]["function"]["name"] == "echo_tool"
    assert result.messages[3]["tool_call_id"] == "call_1"
    assert result.messages[3]["content"] == '{"echo": "ping"}'
    assert provider.requests[1]["messages"][-2]["role"] == "assistant"
    assert provider.requests[1]["messages"][-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_runner_returns_error_result_when_provider_reports_failure() -> None:
    provider = RecordingProvider(
            [
                LLMResponse(
                    content="Error calling LLM: boom",
                    finish_reason="error",
                    error_status_code=400,
                    error_should_retry=False,
                )
            ]
        )
    runner = AgentRunner(provider)
    spec = AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ],
        tools=ToolRegistry(),
        model="dummy-model",
        max_iterations=3,
        max_tool_result_chars=1000,
        error_message="custom error",
    )

    result = await runner.run(spec)

    assert result.stop_reason == "error"
    assert result.final_content == "custom error"
    assert result.error == "Error calling LLM: boom"
    assert result.messages == spec.initial_messages


@pytest.mark.asyncio
async def test_runner_stops_at_max_iterations_after_tool_loop() -> None:
    provider = RecordingProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="call_1", name="echo_tool", arguments={"value": "x"})],
                finish_reason="tool_calls",
            )
        ]
    )
    runner = AgentRunner(provider)
    registry = ToolRegistry()
    registry.register(EchoTool())
    spec = AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "loop"},
        ],
        tools=registry,
        model="dummy-model",
        max_iterations=1,
        max_tool_result_chars=1000,
    )

    result = await runner.run(spec)

    assert result.stop_reason == "max_iterations"
    assert "maximum number of tool iterations" in (result.final_content or "")
    assert result.tools_used == ["echo_tool"]


@pytest.mark.asyncio
async def test_runner_forwards_text_delta_callback() -> None:
    provider = RecordingProvider(
        [
            LLMResponse(
                content="stream me",
                finish_reason="stop",
            )
        ]
    )
    runner = AgentRunner(provider)
    chunks: list[str] = []

    async def on_text_delta(delta: str) -> None:
        chunks.append(delta)

    spec = AgentRunSpec(
        initial_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ],
        tools=ToolRegistry(),
        model="dummy-model",
        max_iterations=3,
        max_tool_result_chars=1000,
        on_text_delta=on_text_delta,
    )

    result = await runner.run(spec)

    assert result.final_content == "stream me"
    assert "".join(chunks) == "stream me"
