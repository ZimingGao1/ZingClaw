"""Sustained-goal tools aligned with ZingClaw session metadata."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from typing import TYPE_CHECKING, Any

from zingclaw.session import GOAL_STATE_KEY, goal_state_raw, parse_goal_state
from zingclaw.session.goal_state import discard_legacy_goal_state_key
from zingclaw.tools.base import Tool, tool_parameters
from zingclaw.tools.context import RequestContext

if TYPE_CHECKING:
    from zingclaw.session import SessionManager


def _iso_now() -> str:
    return datetime.now().isoformat()


class _GoalToolsMixin:
    """Shared request context and session lookup for sustained-goal tools."""

    def __init__(self, sessions: "SessionManager") -> None:
        self._sessions = sessions
        self._request_ctx: ContextVar[RequestContext | None] = ContextVar(
            f"{self.__class__.__name__}_request_ctx",
            default=None,
        )

    def set_context(self, ctx: RequestContext) -> Any:
        return self._request_ctx.set(ctx)

    def reset_context(self, token: Any) -> None:
        self._request_ctx.reset(token)

    def _session(self):
        request_ctx = self._request_ctx.get()
        if request_ctx is None or not request_ctx.session_key:
            return None
        return self._sessions.get_or_create(request_ctx.session_key)


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "One sustained objective for this session. Keep it idempotent, "
                    "self-contained, bounded, and explicit about done-ness."
                ),
                "minLength": 1,
                "maxLength": 12000,
            },
            "ui_summary": {
                "type": ["string", "null"],
                "description": "Optional short label for session status surfaces.",
                "maxLength": 120,
            },
        },
        "required": ["goal"],
        "additionalProperties": False,
    }
)
class LongTaskTool(Tool, _GoalToolsMixin):
    """Mark the current thread as a sustained objective."""

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "sessions", None) is not None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        sessions = getattr(ctx, "sessions", None)
        assert sessions is not None
        return cls(sessions=sessions)

    def __init__(self, sessions: "SessionManager") -> None:
        _GoalToolsMixin.__init__(self, sessions)

    @property
    def name(self) -> str:
        return "long_task"

    @property
    def description(self) -> str:
        return (
            "Mark this thread as a sustained long-running task. Use one active goal per session. "
            "The active goal is persisted in session metadata and mirrored into Runtime Context "
            "on future turns. When the work is done, cancelled, or replaced, call complete_goal."
        )

    async def execute(self, goal: str, ui_summary: str | None = None, **kwargs: Any) -> str:
        del kwargs
        session = self._session()
        if session is None:
            return self.error("long_task requires an active session context.")

        prior = parse_goal_state(goal_state_raw(session.metadata))
        if isinstance(prior, dict) and prior.get("status") == "active":
            return self.error(
                "Error: a sustained goal is already active. "
                "Use complete_goal first, or ask the user before replacing it."
            )

        objective = goal.strip()
        if not objective:
            return self.error("goal must not be empty.")

        summary = (ui_summary or "").strip()[:120]
        session.metadata[GOAL_STATE_KEY] = {
            "status": "active",
            "objective": objective,
            "ui_summary": summary,
            "started_at": _iso_now(),
        }
        discard_legacy_goal_state_key(session.metadata)
        self._sessions.save(session)

        suffix = f"\nSummary line: {summary}" if summary else ""
        return self.ok(
            "Goal recorded. Keep working toward it with normal tools, and call "
            f"complete_goal when the objective is done or intentionally stopped.{suffix}"
        )


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "recap": {
                "type": ["string", "null"],
                "description": (
                    "Short honest recap of what happened: completed, cancelled, "
                    "partially done, or superseded."
                ),
                "maxLength": 8000,
            },
        },
        "additionalProperties": False,
    }
)
class CompleteGoalTool(Tool, _GoalToolsMixin):
    """Mark the active sustained goal finished."""

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "sessions", None) is not None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        sessions = getattr(ctx, "sessions", None)
        assert sessions is not None
        return cls(sessions=sessions)

    def __init__(self, sessions: "SessionManager") -> None:
        _GoalToolsMixin.__init__(self, sessions)

    @property
    def name(self) -> str:
        return "complete_goal"

    @property
    def description(self) -> str:
        return (
            "End bookkeeping for the current sustained goal. Use this when the objective is "
            "finished, cancelled, redirected, or replaced."
        )

    async def execute(self, recap: str | None = None, **kwargs: Any) -> str:
        del kwargs
        session = self._session()
        if session is None:
            return self.error("complete_goal requires an active session context.")

        prior = parse_goal_state(goal_state_raw(session.metadata))
        if not isinstance(prior, dict) or prior.get("status") != "active":
            return self.ok("No active goal to complete.")

        ended_at = _iso_now()
        session.metadata[GOAL_STATE_KEY] = {
            **prior,
            "status": "completed",
            "completed_at": ended_at,
            "recap": (recap or "").strip(),
        }
        discard_legacy_goal_state_key(session.metadata)
        self._sessions.save(session)

        tail = (recap or "").strip()
        if tail:
            return self.ok(f"Goal marked complete ({ended_at}). Recap:\n{tail}")
        return self.ok(f"Goal marked complete ({ended_at}).")
