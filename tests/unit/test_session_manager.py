from __future__ import annotations

import json
from pathlib import Path

from zingclaw.session import SessionManager


def test_session_manager_round_trip_preserves_messages_and_metadata(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("cli:default")
    session.metadata["title"] = "Scratchpad"
    session.add_message("user", "hello")
    session.add_message(
        "assistant",
        "working on it",
        reasoning_content="hidden reasoning",
        partial=True,
    )
    session.add_message(
        "assistant",
        "",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_dir", "arguments": "{}"},
            }
        ],
    )

    manager.save(session)

    reloaded = SessionManager(tmp_path / "sessions").get_or_create("cli:default")
    history = reloaded.get_history()

    assert reloaded.metadata["title"] == "Scratchpad"
    assert [message["role"] for message in reloaded.messages] == ["user", "assistant", "assistant"]
    assert history[1]["reasoning_content"] == "hidden reasoning"
    assert history[1]["partial"] is True
    assert history[2]["tool_calls"][0]["function"]["name"] == "list_dir"

    payload = manager.read_session_file("cli:default")

    assert payload is not None
    assert payload["metadata"]["title"] == "Scratchpad"
    assert payload["messages"][0]["content"] == "hello"


def test_session_get_history_supports_limits_and_timestamp_annotation(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("cli:chat")
    session.add_message("user", "first")
    session.add_message("assistant", "ack")
    session.add_message("user", "second")

    limited = session.get_history(max_messages=2, include_timestamps=True)

    assert len(limited) == 2
    assert limited[0]["role"] == "assistant"
    assert limited[1]["role"] == "user"
    assert limited[1]["content"].startswith("[Message Time: ")
    assert limited[1]["content"].endswith("\nsecond")

    session.add_message("assistant", "B" * 400)
    session.add_message("user", "tail")
    token_limited = session.get_history(max_messages=0, max_tokens=50)

    assert len(token_limited) == 1
    assert token_limited[0]["role"] == "user"
    assert token_limited[0]["content"] == "tail"


def test_session_manager_repairs_corrupt_file_and_lists_preview(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    key = "cli:bad/name?"
    path = manager._get_session_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        {
            "_type": "metadata",
            "key": key,
            "created_at": "2026-06-25T12:00:00",
            "updated_at": "2026-06-25T12:05:00",
            "metadata": {"title": "Recovered"},
            "last_consolidated": 0,
        },
        {"role": "user", "content": "inspect this repo", "timestamp": "2026-06-25T12:00:01"},
        "this is not json",
        {"role": "assistant", "content": "done", "timestamp": "2026-06-25T12:00:02"},
    ]
    path.write_text(
        "\n".join(item if isinstance(item, str) else json.dumps(item) for item in lines) + "\n",
        encoding="utf-8",
    )

    repaired = SessionManager(tmp_path / "sessions").get_or_create(key)

    assert len(repaired.messages) == 2
    assert repaired.messages[0]["content"] == "inspect this repo"

    listing = manager.list_sessions()

    assert listing[0]["key"] == key
    assert listing[0]["title"] == "Recovered"
    assert listing[0]["preview"] == "inspect this repo"
    assert manager.delete_session(key) is True
    assert manager.delete_session(key) is False
