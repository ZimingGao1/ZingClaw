from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from zingclaw.memory import Consolidator, MemoryStore
from zingclaw.providers import LLMProvider, LLMResponse
from zingclaw.session import SessionManager


class SummaryProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
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
        return "summary-model"


@pytest.mark.asyncio
async def test_consolidator_archives_old_session_messages_into_history(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", max_history_entries=20)
    sessions = SessionManager(tmp_path / "sessions")
    session = sessions.get_or_create("cli:demo")
    for idx in range(6):
        session.add_message("user", f"user message {idx}")
        session.add_message("assistant", f"assistant reply {idx}")
    sessions.save(session)

    provider = SummaryProvider([LLMResponse(content="- summarized archived turns", finish_reason="stop")])
    consolidator = Consolidator(
        store,
        provider,
        model="summary-model",
        sessions=sessions,
        consolidation_threshold=4,
        recent_keep_messages=2,
        prompt_token_budget=999_999,
    )

    summary = await consolidator.maybe_consolidate(session)
    refreshed = sessions.get_or_create("cli:demo")
    entries = store.read_history_entries(session_key="cli:demo")

    assert summary == "- summarized archived turns"
    assert refreshed.last_consolidated > 0
    assert refreshed.metadata["_last_summary"]["text"] == "- summarized archived turns"
    assert entries[-1]["content"] == "- summarized archived turns"
    assert "USER: user message 0" in provider.requests[0][1]["content"]
