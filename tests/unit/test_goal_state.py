from __future__ import annotations

from pathlib import Path

from zingclaw.session.goal_state import (
    GOAL_STATE_KEY,
    clear_goal_state,
    discard_legacy_goal_state_key,
    goal_state_public_snapshot,
    goal_state_runtime_lines,
    parse_goal_state,
    runner_wall_llm_timeout_s,
    set_goal_state,
    sustained_goal_active,
    sustained_goal_turn,
)
from zingclaw.session.manager import SessionManager


def test_goal_state_helpers_store_parse_and_render_runtime_lines() -> None:
    metadata: dict[str, object] = {}
    set_goal_state(
        metadata,
        {
            "status": "active",
            "objective": "Inspect the workspace and finish the refactor plan.",
            "ui_summary": "Refactor in progress",
        },
    )

    assert GOAL_STATE_KEY in metadata
    assert parse_goal_state(metadata[GOAL_STATE_KEY]) == {
        "status": "active",
        "objective": "Inspect the workspace and finish the refactor plan.",
        "ui_summary": "Refactor in progress",
    }
    assert sustained_goal_active(metadata) is True
    assert sustained_goal_turn(metadata) is True
    assert goal_state_runtime_lines(metadata) == [
        "Goal (active):",
        "Inspect the workspace and finish the refactor plan.",
        "Summary: Refactor in progress",
    ]
    assert goal_state_public_snapshot(metadata) == {
        "active": True,
        "objective": "Inspect the workspace and finish the refactor plan.",
        "ui_summary": "Refactor in progress",
    }

    clear_goal_state(metadata)
    assert metadata == {}


def test_goal_state_helpers_accept_legacy_json_string() -> None:
    metadata = {
        "thread_goal": '{"status":"active","objective":"Continue indexing","ui_summary":"Indexer"}'
    }

    assert sustained_goal_active(metadata) is True
    assert goal_state_runtime_lines(metadata) == [
        "Goal (active):",
        "Continue indexing",
        "Summary: Indexer",
    ]
    assert goal_state_public_snapshot(metadata) == {
        "active": True,
        "objective": "Continue indexing",
        "ui_summary": "Indexer",
    }


def test_goal_state_helpers_discard_legacy_key_and_hide_completed_state() -> None:
    metadata: dict[str, object] = {
        "thread_goal": {"status": "active", "objective": "Legacy state"},
        GOAL_STATE_KEY: {"status": "completed", "objective": "Done already"},
    }

    discard_legacy_goal_state_key(metadata)

    assert "thread_goal" not in metadata
    assert goal_state_runtime_lines(metadata) == []
    assert goal_state_public_snapshot(metadata) == {"active": False}


def test_runner_wall_llm_timeout_uses_metadata_or_session_goal_state(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path / "sessions")

    assert (
        runner_wall_llm_timeout_s(
            sessions,
            "cli:test",
            metadata={GOAL_STATE_KEY: {"status": "active", "objective": "ship it"}},
        )
        == 0.0
    )
    assert runner_wall_llm_timeout_s(sessions, "cli:test", metadata={}) is None

    session = sessions.get_or_create("cli:test")
    set_goal_state(session.metadata, {"status": "active", "objective": "continue"})
    assert runner_wall_llm_timeout_s(sessions, "cli:test") == 0.0

    clear_goal_state(session.metadata)
    assert runner_wall_llm_timeout_s(sessions, "cli:test") is None
