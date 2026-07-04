from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from zingclaw.memory import Consolidator, DreamConsolidator, MemoryStore
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
    history_markdown = store.history_markdown_file.read_text(encoding="utf-8")

    assert summary == "- summarized archived turns"
    assert refreshed.last_consolidated > 0
    assert refreshed.metadata["_last_summary"]["text"] == "- summarized archived turns"
    assert entries[-1]["content"] == "- summarized archived turns"
    assert "- summarized archived turns" in history_markdown
    assert "USER: user message 0" in provider.requests[0][1]["content"]


@pytest.mark.asyncio
async def test_consolidator_compacts_idle_session_to_recent_legal_suffix(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", max_history_entries=20)
    sessions = SessionManager(tmp_path / "sessions")
    session = sessions.get_or_create("cli:demo")
    for idx in range(6):
        session.add_message("user", f"user message {idx}")
        session.add_message("assistant", f"assistant reply {idx}")
    sessions.save(session)

    provider = SummaryProvider([LLMResponse(content="- idle compacted summary", finish_reason="stop")])
    consolidator = Consolidator(
        store,
        provider,
        model="summary-model",
        sessions=sessions,
        consolidation_threshold=50,
        recent_keep_messages=2,
        prompt_token_budget=999_999,
    )

    summary = await consolidator.compact_idle_session("cli:demo", max_suffix=4)
    refreshed = sessions.get_or_create("cli:demo")
    entries = store.read_history_entries(session_key="cli:demo")
    history_markdown = store.history_markdown_file.read_text(encoding="utf-8")

    assert summary == "- idle compacted summary"
    assert [message["content"] for message in refreshed.messages] == [
        "user message 4",
        "assistant reply 4",
        "user message 5",
        "assistant reply 5",
    ]
    assert refreshed.last_consolidated == 0
    assert refreshed.metadata["_last_summary"]["text"] == "- idle compacted summary"
    assert entries[-1]["content"] == "- idle compacted summary"
    assert "- idle compacted summary" in history_markdown
    assert "USER: user message 0" in provider.requests[0][1]["content"]


@pytest.mark.asyncio
async def test_dream_consolidator_extracts_archived_history_into_memory(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", max_history_entries=20)
    first = store.append_history("user prefers concise Chinese replies", session_key="cli:demo")
    second = store.append_history("project decided to keep trace evidence for regressions")
    provider = SummaryProvider(
        [
            LLMResponse(
                content="- User prefers concise Chinese replies\n- Project keeps trace evidence for regressions",
                finish_reason="stop",
            )
        ]
    )
    dream = DreamConsolidator(store, provider, model="summary-model", max_entries=20)

    result = await dream.run_once()

    memory = store.read_memory()
    assert result.processed_entries == 2
    assert result.last_cursor == second
    assert result.memory_updated is True
    assert store.get_last_dream_cursor() == second
    assert "Dream update from archived history through cursor" in memory
    assert "User prefers concise Chinese replies" in memory
    assert "Project keeps trace evidence for regressions" in memory
    assert f"Cursor {first}" in provider.requests[0][1]["content"]

    second_result = await dream.run_once()

    assert second_result.processed_entries == 0
    assert second_result.last_cursor == second


@pytest.mark.asyncio
async def test_dream_consolidator_advances_cursor_when_nothing_is_durable(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory", max_history_entries=20)
    cursor = store.append_history("one-off greeting with no durable information")
    provider = SummaryProvider([LLMResponse(content="(nothing)", finish_reason="stop")])
    dream = DreamConsolidator(store, provider, model="summary-model", max_entries=20)

    result = await dream.run_once()

    assert result.processed_entries == 1
    assert result.last_cursor == cursor
    assert result.memory_updated is False
    assert store.get_last_dream_cursor() == cursor
    assert store.read_memory() == ""
