from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from zingclaw.agent import AutoCompact
from zingclaw.agent.loop import AgentLoop
from zingclaw.config import Settings
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


def _add_turns(session: Any, turns: int, *, prefix: str = "msg") -> None:
    for index in range(turns):
        session.add_message("user", f"{prefix} user {index}")
        session.add_message("assistant", f"{prefix} assistant {index}")


@pytest.mark.asyncio
async def test_auto_compact_schedules_expired_non_active_sessions(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path / "sessions")
    old_session = sessions.get_or_create("cli:old")
    _add_turns(old_session, 3)
    old_ts = datetime.now() - timedelta(minutes=30)
    old_session.updated_at = old_ts
    old_session.metadata["last_active_at"] = old_ts.isoformat()
    sessions.save(old_session)

    consolidator = MagicMock()
    consolidator.compact_idle_session = AsyncMock(return_value="Summary.")
    auto_compact = AutoCompact(sessions, consolidator, session_ttl_minutes=15)

    scheduled: list[Any] = []

    def schedule_background(coro: Any) -> None:
        scheduled.append(coro)
        coro.close()

    auto_compact.check_expired(schedule_background)

    assert len(scheduled) == 1
    assert "cli:old" in auto_compact._archiving


@pytest.mark.asyncio
async def test_auto_compact_prepare_session_compacts_expired_current_session(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path / "sessions")
    store = MemoryStore(tmp_path / "memory", max_history_entries=20)
    session = sessions.get_or_create("cli:demo")
    _add_turns(session, 6, prefix="idle")
    old_ts = datetime.now() - timedelta(minutes=30)
    session.updated_at = old_ts
    session.metadata["last_active_at"] = old_ts.isoformat()
    sessions.save(session)

    provider = SummaryProvider([LLMResponse(content="- idle summary", finish_reason="stop")])
    consolidator = Consolidator(
        store,
        provider,
        model="summary-model",
        sessions=sessions,
        consolidation_threshold=50,
        recent_keep_messages=2,
        prompt_token_budget=999_999,
    )
    auto_compact = AutoCompact(sessions, consolidator, session_ttl_minutes=15)

    refreshed, formatted_summary = await auto_compact.prepare_session(session, "cli:demo")
    entries = store.read_history_entries(session_key="cli:demo")

    assert formatted_summary is not None
    assert "Previous conversation summary" in formatted_summary
    assert refreshed.metadata["_last_summary"]["text"] == "- idle summary"
    assert [message["content"] for message in refreshed.messages] == [
        "idle user 2",
        "idle assistant 2",
        "idle user 3",
        "idle assistant 3",
        "idle user 4",
        "idle assistant 4",
        "idle user 5",
        "idle assistant 5",
    ]
    assert entries[-1]["content"] == "- idle summary"


@pytest.mark.asyncio
async def test_agent_loop_compacts_expired_session_before_new_turn(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    settings.agent.session_ttl_minutes = 15
    provider = SummaryProvider(
        [
            LLMResponse(content="- archived idle context", finish_reason="stop"),
            LLMResponse(content="fresh answer", finish_reason="stop"),
        ]
    )
    loop = AgentLoop(settings=settings, provider=provider)

    session = loop.sessions.get_or_create("cli:demo")
    _add_turns(session, 6, prefix="old")
    old_ts = datetime.now() - timedelta(minutes=30)
    session.updated_at = old_ts
    session.metadata["last_active_at"] = old_ts.isoformat()
    loop.sessions.save(session)

    result = await loop.process_message(
        "new question",
        session_key="cli:demo",
        channel="cli",
        chat_id="demo",
    )

    refreshed = loop.sessions.get_or_create("cli:demo")
    entries = loop.memory.read_history_entries(session_key="cli:demo")
    prompt = provider.requests[-1][0]["content"]

    assert result.final_content == "fresh answer"
    assert refreshed.metadata["_last_summary"]["text"] == "- archived idle context"
    assert entries[-1]["content"] == "- archived idle context"
    assert refreshed.messages[-2]["content"] == "new question"
    assert refreshed.messages[-1]["content"] == "fresh answer"
    assert "[Archived Context Summary]" in prompt
    assert "- archived idle context" in prompt
