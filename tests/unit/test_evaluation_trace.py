from __future__ import annotations

from typing import Any

import pytest

from zingclaw.agent.loop import AgentLoop
from zingclaw.bus import ToolCallStarted, build_runtime_event_context
from zingclaw.config import Settings
from zingclaw.evaluation import TraceStore
from zingclaw.providers import LLMProvider, LLMResponse
from zingclaw.tools import ToolRegistry


class StaticProvider(LLMProvider):
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
        return LLMResponse(content="trace ok", finish_reason="stop")

    def get_default_model(self) -> str:
        return "static-model"


def test_trace_recorder_writes_jsonl_and_redacts_sensitive_values(tmp_path) -> None:
    store = TraceStore(tmp_path / "traces")
    recorder = store.start_trace(metadata={"case": "redaction", "api_key": "secret"})
    context = build_runtime_event_context(
        channel="eval",
        chat_id="demo",
        session_key="eval:demo",
        metadata={"authorization": "Bearer secret"},
    )

    recorder.record_input(
        user_input="hello",
        session_key="eval:demo",
        channel="eval",
        chat_id="demo",
        metadata={"token": "secret"},
    )
    recorder.record_runtime_event(
        ToolCallStarted(
            context=context,
            iteration=1,
            tool_name="echo_tool",
            tool_call_id="call_1",
            arguments={"value": "ping", "password": "secret"},
        )
    )
    recorder.finalize(status="completed")

    events = recorder.read_events()
    metadata = recorder.metadata_path.read_text(encoding="utf-8")
    raw_trace = recorder.events_path.read_text(encoding="utf-8")

    assert [event["type"] for event in events] == ["user_input", "tool_started"]
    assert events[1]["tool_name"] == "echo_tool"
    assert events[1]["arguments"] == {"value": "ping", "password": "[REDACTED]"}
    assert "event_class" not in raw_trace
    assert '"context"' not in raw_trace
    assert recorder.trace_id in metadata
    assert "secret" not in raw_trace
    assert "[REDACTED]" in raw_trace


@pytest.mark.asyncio
async def test_agent_loop_writes_compact_turn_trace(tmp_path) -> None:
    loop = AgentLoop(
        settings=Settings(_env_file=None, home_dir=tmp_path),
        provider=StaticProvider(api_key=None, api_base=None),
        tools=ToolRegistry(),
    )

    try:
        result = await loop.process_message(
            "hello trace",
            session_key="trace-demo",
            channel="cli",
            chat_id="trace-demo",
        )
    finally:
        await loop.aclose()

    trace_dirs = sorted((tmp_path / "traces").iterdir())
    assert result.trace_id is not None
    assert [path.name for path in trace_dirs] == [result.trace_id]

    metadata = (trace_dirs[0] / "metadata.json").read_text(encoding="utf-8")
    raw_trace = (trace_dirs[0] / "trace.jsonl").read_text(encoding="utf-8")
    events = TraceStore(tmp_path / "traces").read_events(result.trace_id)

    assert '"workflow_name": "agent_turn"' in metadata
    assert '"session_key": "trace-demo"' in metadata
    assert [event["type"] for event in events] == [
        "user_input",
        "turn_started",
        "status",
        "status",
        "status",
        "status",
        "turn_completed",
        "turn_result",
    ]
    assert events[2]["status"] == "building_context"
    assert events[4]["status"] == "calling_model"
    assert events[-2]["final_content"] == "trace ok"
    assert "final_content" not in events[-1]
    assert "event_class" not in raw_trace
    assert "hello trace" in raw_trace
    assert "trace ok" in raw_trace
    assert raw_trace.count("trace ok") == 1
