from __future__ import annotations

from zingclaw.session.turn_continuation import (
    GOAL_CONTINUATION_ROUNDS_KEY,
    INTERNAL_CONTINUATION_KIND_META,
    INTERNAL_CONTINUATION_META,
    INTERNAL_CONTINUATION_PENDING_META,
    INTERNAL_CONTINUATION_RUN_STARTED_AT_META,
    SKIP_USER_PERSIST_META,
    build_goal_continuation_prompt,
    build_internal_continuation_metadata,
    clear_internal_continuation_state,
    goal_continuation_available,
    increment_goal_continuation_round,
    internal_continuation_inbound,
    internal_continuation_pending,
    internal_continuation_run_started_at,
    mark_internal_continuation_pending,
    should_finalize_on_max_iterations,
    should_persist_user_message,
    should_stream_budget_response,
)


def test_build_internal_continuation_metadata_strips_stream_flags() -> None:
    metadata = build_internal_continuation_metadata(
        {
            "_stream_delta": True,
            "_stream_end": True,
            "_resuming": True,
            "original_command": "/goal",
        },
        run_started_at=12.5,
    )

    assert metadata[INTERNAL_CONTINUATION_META] is True
    assert metadata[INTERNAL_CONTINUATION_KIND_META] == "sustained_goal"
    assert metadata[INTERNAL_CONTINUATION_RUN_STARTED_AT_META] == 12.5
    assert "_stream_delta" not in metadata
    assert "_stream_end" not in metadata
    assert "_resuming" not in metadata
    assert internal_continuation_inbound(metadata) is True
    assert internal_continuation_run_started_at(metadata) == 12.5
    assert should_persist_user_message(metadata) is False


def test_goal_continuation_round_helpers_track_budget_state() -> None:
    session_metadata = {
        "goal_state": {"status": "active", "objective": "Finish indexing"},
    }

    assert goal_continuation_available(session_metadata) is True
    increment_goal_continuation_round(session_metadata)
    assert session_metadata[GOAL_CONTINUATION_ROUNDS_KEY] == 1

    session_metadata[GOAL_CONTINUATION_ROUNDS_KEY] = 12
    assert goal_continuation_available(session_metadata) is False

    clear_internal_continuation_state(session_metadata)
    assert session_metadata[GOAL_CONTINUATION_ROUNDS_KEY] == 12

    session_metadata["goal_state"] = {"status": "complete"}
    clear_internal_continuation_state(session_metadata)
    assert GOAL_CONTINUATION_ROUNDS_KEY not in session_metadata


def test_should_persist_user_message_respects_skip_flag() -> None:
    assert should_persist_user_message({SKIP_USER_PERSIST_META: True}) is False


def test_turn_continuation_helpers_cover_budget_boundary_state() -> None:
    assert should_stream_budget_response(stop_reason="completed", continuation_available=True) is True
    assert should_stream_budget_response(stop_reason="max_iterations", continuation_available=False) is True
    assert should_stream_budget_response(stop_reason="max_iterations", continuation_available=True) is False
    assert should_finalize_on_max_iterations(continuation_available=False) is True
    assert should_finalize_on_max_iterations(continuation_available=True) is False


def test_goal_continuation_prompt_and_pending_marker() -> None:
    metadata = {
        "goal_state": {
            "status": "active",
            "objective": "Finish the migration.",
            "ui_summary": "migration",
        }
    }
    message_metadata: dict[str, object] = {}

    prompt = build_goal_continuation_prompt(metadata)
    mark_internal_continuation_pending(message_metadata)

    assert "Finish the migration." in prompt
    assert "complete_goal" in prompt
    assert message_metadata[INTERNAL_CONTINUATION_PENDING_META] is True
    assert internal_continuation_pending(message_metadata) is True
