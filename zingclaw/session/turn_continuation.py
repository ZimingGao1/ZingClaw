"""Reserved helpers for internal turn continuation metadata.

These helpers keep budget-boundary continuation policy out of ``AgentLoop``.
"""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping

from zingclaw.session.goal_state import goal_state_runtime_lines, sustained_goal_active, sustained_goal_turn

INTERNAL_CONTINUATION_META = "_internal_continuation"
INTERNAL_CONTINUATION_KIND_META = "_internal_continuation_kind"
INTERNAL_CONTINUATION_PENDING_META = "_internal_continuation_pending"
INTERNAL_CONTINUATION_RUN_STARTED_AT_META = "_internal_continuation_run_started_at"
SKIP_USER_PERSIST_META = "_skip_user_persist"

GOAL_CONTINUATION_KIND = "sustained_goal"
GOAL_CONTINUATION_SENDER = "system:continuation"
GOAL_CONTINUATION_ROUNDS_KEY = "_sustained_goal_continuation_rounds"
MAX_GOAL_CONTINUATION_ROUNDS = 12

_STRIPPED_INBOUND_META_KEYS = {
    "_stream_id",
    "_stream_delta",
    "_stream_end",
    "_resuming",
    INTERNAL_CONTINUATION_PENDING_META,
}


def internal_continuation_inbound(metadata: Mapping[str, Any] | None) -> bool:
    """True when the inbound message was created internally by continuation logic."""
    return bool(metadata and metadata.get(INTERNAL_CONTINUATION_META) is True)


def internal_continuation_pending(metadata: Mapping[str, Any] | None) -> bool:
    """True when the current turn queued an invisible continuation slice."""
    return bool(metadata and metadata.get(INTERNAL_CONTINUATION_PENDING_META) is True)


def internal_continuation_run_started_at(metadata: Mapping[str, Any] | None) -> float | None:
    """Return the user-visible run start propagated across continuation slices."""
    if not metadata:
        return None
    value = metadata.get(INTERNAL_CONTINUATION_RUN_STARTED_AT_META)
    if not isinstance(value, int | float):
        return None
    started_at = float(value)
    return started_at if started_at > 0 else None


def should_persist_user_message(metadata: Mapping[str, Any] | None) -> bool:
    """Return whether the inbound turn should be persisted as a user message."""
    if metadata and metadata.get(SKIP_USER_PERSIST_META) is True:
        return False
    return not internal_continuation_inbound(metadata)


def should_finalize_on_max_iterations(
    *,
    continuation_available: bool,
) -> bool:
    """Return whether a max-iteration boundary should produce a final response."""
    return not continuation_available


def should_stream_budget_response(
    *,
    stop_reason: str,
    continuation_available: bool,
) -> bool:
    """Return whether the current slice should stream a budget-boundary reply."""
    if stop_reason != "max_iterations":
        return True
    return should_finalize_on_max_iterations(continuation_available=continuation_available)


def clear_internal_continuation_state(metadata: MutableMapping[str, Any]) -> None:
    """Reset continuation-round bookkeeping when the owning mode is inactive."""
    if not sustained_goal_active(metadata):
        metadata.pop(GOAL_CONTINUATION_ROUNDS_KEY, None)


def goal_continuation_available(
    session_metadata: Mapping[str, Any] | None,
    *,
    message_metadata: Mapping[str, Any] | None = None,
    max_rounds: int = MAX_GOAL_CONTINUATION_ROUNDS,
) -> bool:
    """True when a sustained-goal turn may schedule another internal slice."""
    if not sustained_goal_turn(session_metadata, message_metadata=message_metadata):
        return False
    if not sustained_goal_active(session_metadata):
        return False
    try:
        rounds = int((session_metadata or {}).get(GOAL_CONTINUATION_ROUNDS_KEY) or 0)
    except (TypeError, ValueError):
        rounds = 0
    return rounds < max(0, max_rounds)


def increment_goal_continuation_round(session_metadata: MutableMapping[str, Any]) -> None:
    """Advance the per-session continuation round counter."""
    try:
        rounds = int(session_metadata.get(GOAL_CONTINUATION_ROUNDS_KEY) or 0)
    except (TypeError, ValueError):
        rounds = 0
    session_metadata[GOAL_CONTINUATION_ROUNDS_KEY] = rounds + 1


def mark_internal_continuation_pending(message_metadata: MutableMapping[str, Any]) -> None:
    """Mark that the current visible turn scheduled another hidden slice."""
    message_metadata[INTERNAL_CONTINUATION_PENDING_META] = True


def build_internal_continuation_metadata(
    message_metadata: Mapping[str, Any] | None,
    *,
    run_started_at: float | None = None,
    kind: str = GOAL_CONTINUATION_KIND,
) -> dict[str, Any]:
    """Clone inbound metadata and convert it into continuation-safe form."""
    metadata = dict(message_metadata or {})
    metadata[INTERNAL_CONTINUATION_META] = True
    metadata[INTERNAL_CONTINUATION_KIND_META] = kind
    if run_started_at is not None:
        metadata[INTERNAL_CONTINUATION_RUN_STARTED_AT_META] = float(run_started_at)
    for key in _STRIPPED_INBOUND_META_KEYS:
        metadata.pop(key, None)
    return metadata


def build_goal_continuation_prompt(metadata: Mapping[str, Any] | None) -> str:
    """Build the synthetic prompt for the next sustained-goal slice."""
    lines = goal_state_runtime_lines(metadata)
    if lines:
        goal = "\n".join(lines)
        return (
            "Continue the active sustained goal after the previous turn reached "
            "its tool-call budget.\n\n"
            f"{goal}\n\n"
            "Continue from the saved context. Do not mention the continuation "
            "boundary to the user. Use tools as needed, and call complete_goal "
            "when the objective is truly finished."
        )
    return (
        "Continue the active sustained goal after the previous turn reached "
        "its tool-call budget. Continue from the saved context. Do not mention "
        "the continuation boundary to the user. Use tools as needed, and call "
        "complete_goal when the objective is truly finished."
    )
