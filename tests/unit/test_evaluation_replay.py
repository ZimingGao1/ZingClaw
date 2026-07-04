from __future__ import annotations

from typing import Any

import pytest

from zingclaw.agent.loop import AgentLoop
from zingclaw.config import Settings
from zingclaw.evaluation import ReplayCase, TraceStore, run_replay_case
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
        del messages, tools, model, max_tokens, temperature, reasoning_effort, tool_choice, on_text_delta
        return self.responses.pop(0)

    def get_default_model(self) -> str:
        return "dummy-model"


class EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "Echo a value."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return f"echo:{kwargs['value']}"


@pytest.mark.asyncio
async def test_run_replay_case_generates_fresh_trace_and_scorecard(tmp_path) -> None:
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
    loop = AgentLoop(
        settings=Settings(_env_file=None, home_dir=tmp_path),
        provider=provider,
        tools=registry,
    )
    case = ReplayCase(
        name="echo_case",
        user_input="use echo",
        session_key="eval:echo",
        expected_tools=["echo_tool"],
        allow_tool_errors=False,
    )

    try:
        result = await run_replay_case(loop, case, TraceStore(tmp_path / "traces"))
    finally:
        await loop.aclose()

    assert result.scorecard.passed is True
    assert result.scorecard.score == 100
    assert (result.trace_dir / "trace.jsonl").exists()
    assert (result.trace_dir / "metadata.json").exists()
    assert (result.trace_dir / "scorecard.json").exists()
    trace_text = (result.trace_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert '"type": "user_input"' in trace_text
    assert '"type": "tool_started"' in trace_text
    assert '"tool_name": "echo_tool"' in trace_text
