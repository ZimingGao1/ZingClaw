from __future__ import annotations

from typing import Any

import pytest

from zingclaw.providers.base import (
    LLMProvider,
    LLMResponse,
    ToolCallRequest,
    parse_tool_arguments,
    tool_arguments_object_for_replay,
)
import zingclaw.providers.base as provider_base


class DummyProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]):
        super().__init__(api_key=None, api_base=None)
        self._responses = list(responses)
        self.calls = 0

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
        self.calls += 1
        return self._responses.pop(0)

    def get_default_model(self) -> str:
        return "dummy-model"


def test_parse_tool_arguments_handles_empty_and_json() -> None:
    assert parse_tool_arguments(None) == {}
    assert parse_tool_arguments("") == {}
    assert parse_tool_arguments('{"path": "."}') == {"path": "."}
    assert parse_tool_arguments("not-json") == "not-json"


def test_tool_arguments_object_for_replay_can_use_json_repair(monkeypatch) -> None:
    class FakeJsonRepair:
        @staticmethod
        def loads(value: str):
            assert value == '{"path": ".",}'
            return {"path": "."}

    monkeypatch.setattr(provider_base, "json_repair", FakeJsonRepair)

    assert tool_arguments_object_for_replay('{"path": ".",}') == {"path": "."}


def test_llm_response_executes_tools_only_for_tool_capable_reasons() -> None:
    response = LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
        finish_reason="tool_calls",
    )
    assert response.should_execute_tools is True

    blocked = LLMResponse(
        content=None,
        tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": "."})],
        finish_reason="error",
    )
    assert blocked.should_execute_tools is False


@pytest.mark.asyncio
async def test_chat_with_retry_retries_transient_error_once() -> None:
    provider = DummyProvider(
        [
            LLMResponse(
                content="Error calling LLM: rate limit exceeded",
                finish_reason="error",
                error_status_code=429,
            ),
            LLMResponse(content="ok", finish_reason="stop"),
        ]
    )
    provider._CHAT_RETRY_DELAYS = (0.0,)

    response = await provider.chat_with_retry(messages=[{"role": "user", "content": "hi"}])

    assert response.content == "ok"
    assert provider.calls == 2
