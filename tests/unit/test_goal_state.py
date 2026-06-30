from __future__ import annotations

from zingclaw.session.goal_state import (
    GOAL_STATE_KEY,
    clear_goal_state,
    goal_state_public_snapshot,
    goal_state_runtime_lines,
    parse_goal_state,
    set_goal_state,
    sustained_goal_active,
    sustained_goal_turn,
)


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
