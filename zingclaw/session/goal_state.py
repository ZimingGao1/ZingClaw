"""Session metadata helpers for sustained-goal state."""

from __future__ import annotations

import json
from typing import Any, Mapping, MutableMapping

from zingclaw.session.manager import SessionManager

GOAL_STATE_KEY = "goal_state"
_LEGACY_GOAL_STATE_SESSION_KEY = "thread_goal"
_MAX_OBJECTIVE_IN_RUNTIME = 4_000
_MAX_OBJECTIVE_PUBLIC = 600


def _session_goal_raw(metadata: Mapping[str, Any] | None) -> Any:
    if not metadata:
        return None
    if GOAL_STATE_KEY in metadata:
        return metadata.get(GOAL_STATE_KEY)
    return metadata.get(_LEGACY_GOAL_STATE_SESSION_KEY)


def discard_legacy_goal_state_key(metadata: MutableMapping[str, Any]) -> None:
    """Remove the old goal-state metadata key after migrating writes."""
    metadata.pop(_LEGACY_GOAL_STATE_SESSION_KEY, None)


def goal_state_raw(metadata: Mapping[str, Any] | None) -> Any:
    """Return the stored goal-state blob from modern or legacy metadata keys."""
    return _session_goal_raw(metadata)


def parse_goal_state(blob: Any) -> dict[str, Any] | None:
    """Parse a goal-state blob stored as either dict or JSON string."""
    if blob is None:
        return None
    if isinstance(blob, dict):
        return blob
    if isinstance(blob, str):
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def set_goal_state(metadata: MutableMapping[str, Any], goal: Mapping[str, Any] | None) -> None:
    """Store goal state on session metadata under the canonical key."""
    discard_legacy_goal_state_key(metadata)
    if goal is None:
        metadata.pop(GOAL_STATE_KEY, None)
        return
    metadata[GOAL_STATE_KEY] = dict(goal)


def clear_goal_state(metadata: MutableMapping[str, Any]) -> None:
    """Clear both canonical and legacy goal-state keys."""
    metadata.pop(GOAL_STATE_KEY, None)
    discard_legacy_goal_state_key(metadata)


def sustained_goal_active(metadata: Mapping[str, Any] | None) -> bool:
    """True when the session stores an active sustained-goal state."""
    goal = parse_goal_state(goal_state_raw(metadata))
    return isinstance(goal, dict) and goal.get("status") == "active"


def sustained_goal_turn(
    metadata: Mapping[str, Any] | None,
    *,
    message_metadata: Mapping[str, Any] | None = None,
) -> bool:
    """True when the current turn should use sustained-goal runtime policy."""
    if sustained_goal_active(metadata):
        return True
    if not message_metadata:
        return False
    return str(message_metadata.get("original_command") or "").strip() == "/goal"


def goal_state_runtime_lines(metadata: Mapping[str, Any] | None) -> list[str]:
    """Lines suitable for inclusion in a runtime context block."""
    goal = parse_goal_state(goal_state_raw(metadata))
    if not isinstance(goal, dict) or goal.get("status") != "active":
        return []

    objective = str(goal.get("objective") or "").strip()
    if not objective:
        return ["Goal: active (no objective text stored)."]
    if len(objective) > _MAX_OBJECTIVE_IN_RUNTIME:
        objective = objective[:_MAX_OBJECTIVE_IN_RUNTIME].rstrip() + "\n...(truncated)"

    lines = ["Goal (active):", objective]
    summary = str(goal.get("ui_summary") or "").strip()
    if summary:
        lines.append(f"Summary: {summary}")
    return lines


def goal_state_public_snapshot(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a JSON-safe summary for future CLI/WebUI status surfaces."""
    goal = parse_goal_state(goal_state_raw(metadata))
    if not isinstance(goal, dict) or goal.get("status") != "active":
        return {"active": False}

    objective = str(goal.get("objective") or "").strip()
    if len(objective) > _MAX_OBJECTIVE_PUBLIC:
        objective = objective[:_MAX_OBJECTIVE_PUBLIC].rstrip() + "..."

    snapshot: dict[str, Any] = {"active": True}
    summary = str(goal.get("ui_summary") or "").strip()[:120]
    if summary:
        snapshot["ui_summary"] = summary
    if objective:
        snapshot["objective"] = objective
    return snapshot


def runner_wall_llm_timeout_s(
    sessions: SessionManager,
    session_key: str | None,
    *,
    metadata: Mapping[str, Any] | None = None,
    message_metadata: Mapping[str, Any] | None = None,
) -> float | None:
    """Return a future runner override for sustained-goal turns.

    ``0.0`` means "do not wrap the LLM request in a wall-clock wait_for".
    ``None`` means "use default timeout policy".
    """
    session_metadata = metadata
    if session_metadata is None and session_key:
        session_metadata = sessions.get_or_create(session_key).metadata
    return 0.0 if sustained_goal_turn(session_metadata, message_metadata=message_metadata) else None
