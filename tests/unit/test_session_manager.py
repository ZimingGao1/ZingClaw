from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from zingclaw.session import SessionManager, set_goal_state


def _assert_no_orphans(history: list[dict]) -> None:
    declared = {
        tool_call["id"]
        for message in history
        if message.get("role") == "assistant"
        for tool_call in (message.get("tool_calls") or [])
        if isinstance(tool_call, dict) and tool_call.get("id")
    }
    orphan_ids = [
        message.get("tool_call_id")
        for message in history
        if message.get("role") == "tool" and message.get("tool_call_id") not in declared
    ]
    assert orphan_ids == [], f"orphan tool_call_ids: {orphan_ids}"


def _tool_turn(prefix: str, idx: int) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"{prefix}_{idx}_a",
                    "type": "function",
                    "function": {"name": "x", "arguments": "{}"},
                },
                {
                    "id": f"{prefix}_{idx}_b",
                    "type": "function",
                    "function": {"name": "y", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": f"{prefix}_{idx}_a", "name": "x", "content": "ok"},
        {"role": "tool", "tool_call_id": f"{prefix}_{idx}_b", "name": "y", "content": "ok"},
    ]


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

    assert len(limited) == 1
    assert limited[0]["role"] == "user"
    assert limited[0]["content"].startswith("[Message Time: ")
    assert limited[0]["content"].endswith("\nsecond")

    session.add_message("assistant", "B" * 400)
    session.add_message("user", "tail")
    token_limited = session.get_history(max_messages=0, max_tokens=50)

    assert len(token_limited) == 1
    assert token_limited[0]["role"] == "user"
    assert token_limited[0]["content"] == "tail"


def test_session_get_history_drops_orphan_tool_results_when_window_cuts_tool_calls(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("cli:orphan-tools")
    session.messages.append({"role": "user", "content": "old turn"})
    for index in range(20):
        session.messages.extend(_tool_turn("old", index))
    session.messages.append({"role": "user", "content": "problem turn"})
    for index in range(25):
        session.messages.extend(_tool_turn("cur", index))
    session.messages.append({"role": "user", "content": "latest question"})

    history = session.get_history(max_messages=100)

    _assert_no_orphans(history)
    assert history[-1]["content"] == "latest question"


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


def test_session_metadata_helpers_surface_state_in_listing_and_metadata(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("cli:state")
    set_goal_state(
        session.metadata,
        {
            "status": "active",
            "objective": "Finish the roadmap and verify the chat loop.",
            "ui_summary": "Roadmap",
        },
    )
    session.add_message("user", "draft the roadmap")
    manager.save(session)

    manager.rename_session("cli:state", "Roadmap Draft")
    manager.set_session_tags("cli:state", ["planning", "nanobot", "planning", ""])
    compacted_at = datetime(2026, 6, 27, 21, 30, 0)
    next_eligible_at = datetime(2026, 6, 27, 22, 0, 0)
    manager.mark_session_compacted(
        "cli:state",
        "Compressed older turns into a short summary.",
        compacted_at=compacted_at,
        trigger="manual",
        next_eligible_at=next_eligible_at,
    )

    metadata = manager.read_session_metadata("cli:state")
    assert metadata is not None
    assert metadata["title"] == "Roadmap Draft"
    assert metadata["tags"] == ["planning", "nanobot"]
    assert metadata["summary"] == "Compressed older turns into a short summary."
    assert metadata["goal_state"] == {
        "active": True,
        "objective": "Finish the roadmap and verify the chat loop.",
        "ui_summary": "Roadmap",
    }
    assert metadata["last_compacted_at"] == compacted_at.isoformat()
    assert metadata["auto_compact_eligible_at"] == next_eligible_at.isoformat()
    assert metadata["message_count"] == 1
    assert metadata["metadata"]["title_user_edited"] is True
    assert metadata["metadata"]["last_active_at"]

    listing = manager.list_sessions()
    assert listing[0]["key"] == "cli:state"
    assert listing[0]["title"] == "Roadmap Draft"
    assert listing[0]["tags"] == ["planning", "nanobot"]
    assert listing[0]["summary"] == "Compressed older turns into a short summary."
    assert listing[0]["goal_state"] == {
        "active": True,
        "objective": "Finish the roadmap and verify the chat loop.",
        "ui_summary": "Roadmap",
    }
    assert listing[0]["message_count"] == 1
    assert listing[0]["last_active_at"]
    assert listing[0]["preview"] == "draft the roadmap"


def test_fork_session_before_user_index_drops_volatile_state_and_keeps_prefix(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    source = manager.get_or_create("cli:source")
    source.metadata.update(
        {
            "goal_state": {"status": "active", "objective": "finish the refactor"},
            "pending_user_turn": {"resume": True},
            "runtime_checkpoint": {"step": 2},
            "thread_goal": {"legacy": True},
            "title": "Original Title",
            "title_user_edited": True,
            "_last_summary": {"text": "old archived summary"},
        }
    )
    source.add_message("user", "first user turn")
    source.add_message("assistant", "first answer")
    source.add_message("user", "second user turn")
    source.add_message("assistant", "second answer")
    source.last_consolidated = len(source.messages)
    manager.save(source)

    forked = manager.fork_session_before_user_index("cli:source", "cli:fork", before_user_index=1)

    assert forked is not None
    assert [message["content"] for message in forked.messages] == [
        "first user turn",
        "first answer",
    ]
    assert forked.last_consolidated == 0
    assert "goal_state" not in forked.metadata
    assert "pending_user_turn" not in forked.metadata
    assert "runtime_checkpoint" not in forked.metadata
    assert "thread_goal" not in forked.metadata
    assert "title" not in forked.metadata
    assert "_last_summary" not in forked.metadata


def test_session_retain_recent_legal_suffix_returns_dropped_messages_and_adjusts_last_consolidated(
    tmp_path: Path,
) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("cli:trim")
    for index in range(10):
        session.messages.append({"role": "user", "content": f"msg{index}"})
    session.last_consolidated = 7

    dropped, already_consolidated = session.retain_recent_legal_suffix(4)

    assert [message["content"] for message in dropped] == [f"msg{index}" for index in range(6)]
    assert already_consolidated == 6
    assert [message["content"] for message in session.messages] == ["msg6", "msg7", "msg8", "msg9"]
    assert session.last_consolidated == 1


def test_session_retain_recent_legal_suffix_can_extend_to_user_for_long_recent_turn(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("cli:extend")
    session.messages.append({"role": "user", "content": "old"})
    session.messages.append({"role": "assistant", "content": "old answer"})
    session.messages.append({"role": "user", "content": "record this"})
    for index in range(4):
        session.messages.extend(_tool_turn("recent", index))
    session.messages.append({"role": "assistant", "content": "done"})

    dropped, _ = session.retain_recent_legal_suffix(8, extend_to_user=True)

    assert dropped
    assert len(session.messages) > 8
    assert session.messages[0]["content"] == "record this"
    assert session.messages[-1]["content"] == "done"
    _assert_no_orphans(session.get_history(max_messages=500))


def test_session_enforce_file_cap_archives_only_unconsolidated_dropped_prefix(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("cli:file-cap")
    for index in range(10):
        session.messages.append({"role": "user", "content": f"u{index}"})
    for index in range(10):
        session.messages.append({"role": "assistant", "content": f"a{index}"})
    session.last_consolidated = 8

    archived: list[list[dict]] = []
    session.enforce_file_cap(lambda messages: archived.append(list(messages)), limit=4)

    assert len(session.messages) == 4
    assert [message["content"] for message in session.messages] == ["u9", "a0", "a1", "a2"]
    assert session.last_consolidated == 0
    assert len(archived) == 1
    assert [message["content"] for message in archived[0]] == ["u8", "a3", "a4", "a5", "a6", "a7", "a8", "a9"]


def test_session_retain_recent_legal_suffix_recomputes_last_consolidated_from_retained_membership(
    tmp_path: Path,
) -> None:
    manager = SessionManager(tmp_path / "sessions")
    session = manager.get_or_create("cli:last-consolidated")
    for index in range(10):
        session.messages.append({"role": "user", "content": f"u{index}"})
    for index in range(10):
        session.messages.append({"role": "assistant", "content": f"a{index}"})
    session.last_consolidated = 12

    dropped, already_consolidated = session.retain_recent_legal_suffix(4)

    assert [message["content"] for message in session.messages] == ["u9", "a0", "a1", "a2"]
    assert session.last_consolidated == 3
    assert already_consolidated == 9
    assert [message["content"] for message in dropped[:3]] == ["u0", "u1", "u2"]
