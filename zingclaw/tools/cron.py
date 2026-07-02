"""Cron tool for scheduling reminders and recurring tasks."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from zingclaw.cron import CronSchedule
from zingclaw.tools.base import Tool, tool_parameters
from zingclaw.tools.context import RequestContext

if TYPE_CHECKING:
    from zingclaw.cron import CronService


class _CronContextMixin:
    """Track origin session and delivery context for cron jobs."""

    def __init__(self) -> None:
        self._request_ctx: ContextVar[RequestContext | None] = ContextVar(
            f"{self.__class__.__name__}_request_ctx",
            default=None,
        )

    def set_context(self, ctx: RequestContext) -> Any:
        return self._request_ctx.set(ctx)

    def reset_context(self, token: Any) -> None:
        self._request_ctx.reset(token)

    def _current_request_context(self) -> RequestContext | None:
        return self._request_ctx.get()


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "remove"],
                "description": "Action to perform.",
            },
            "name": {
                "type": ["string", "null"],
                "description": "Optional short label for the job.",
                "maxLength": 120,
            },
            "message": {
                "type": ["string", "null"],
                "description": "Required when action='add'. The reminder or task text to emit when triggered.",
            },
            "every_seconds": {
                "type": ["integer", "null"],
                "description": "Recurring interval in seconds.",
                "minimum": 1,
            },
            "cron_expr": {
                "type": ["string", "null"],
                "description": "Cron expression like '0 9 * * *'.",
            },
            "tz": {
                "type": ["string", "null"],
                "description": "Optional IANA timezone used with cron_expr.",
            },
            "at": {
                "type": ["string", "null"],
                "description": "One-time ISO datetime, for example 2026-07-01T09:00:00.",
            },
            "job_id": {
                "type": ["string", "null"],
                "description": "Required when action='remove'.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }
)
class CronTool(Tool, _CronContextMixin):
    """Schedule reminders and recurring tasks."""

    _scopes = {"core"}

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "cron_service", None) is not None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        service = getattr(ctx, "cron_service", None)
        assert service is not None
        return cls(service, default_timezone=getattr(ctx, "timezone", "UTC"))

    def __init__(self, cron_service: "CronService", *, default_timezone: str = "UTC") -> None:
        _CronContextMixin.__init__(self)
        self._cron = cron_service
        self._default_timezone = default_timezone

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return (
            "Schedule reminders and recurring tasks. "
            "Actions: add, list, remove. "
            f"If tz is omitted, cron expressions and naive ISO times default to {self._default_timezone}."
        )

    async def execute(
        self,
        action: str,
        name: str | None = None,
        message: str | None = None,
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        del kwargs
        if action == "add":
            return self._add_job(
                name=name,
                message=str(message or ""),
                every_seconds=every_seconds,
                cron_expr=cron_expr,
                tz=tz,
                at=at,
            )
        if action == "list":
            return self._list_jobs()
        if action == "remove":
            return self._remove_job(job_id)
        return self.error(f"unknown action '{action}'")

    def _add_job(
        self,
        *,
        name: str | None,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
    ) -> str:
        request_ctx = self._current_request_context()
        if request_ctx is None or not request_ctx.session_key or not request_ctx.channel or not request_ctx.chat_id:
            return self.error("scheduled cron jobs must be created from an active chat session.")
        if not message.strip():
            return self.error("message is required when action='add'")

        schedule_count = sum(
            1
            for value in (every_seconds, cron_expr, at)
            if value not in (None, "", 0)
        )
        if schedule_count != 1:
            return self.error("provide exactly one schedule: every_seconds, cron_expr, or at")
        if tz and not cron_expr:
            return self.error("tz can only be used with cron_expr")
        if tz and not self._is_valid_timezone(tz):
            return self.error(f"unknown timezone '{tz}'")

        delete_after_run = False
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=int(every_seconds) * 1000)
        elif cron_expr:
            effective_tz = tz or self._default_timezone
            if not self._is_valid_timezone(effective_tz):
                return self.error(f"unknown timezone '{effective_tz}'")
            schedule = CronSchedule(kind="cron", expr=str(cron_expr).strip(), tz=effective_tz)
        else:
            assert at is not None
            try:
                dt = datetime.fromisoformat(str(at).strip())
            except ValueError:
                return self.error(f"invalid ISO datetime format '{at}'")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self._zoneinfo(self._default_timezone))
            schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
            delete_after_run = True

        label = name.strip() if isinstance(name, str) and name.strip() else message[:30]
        job = self._cron.add_job(
            name=label,
            schedule=schedule,
            message=message,
            session_key=request_ctx.session_key,
            origin_channel=request_ctx.channel,
            origin_chat_id=request_ctx.chat_id,
            origin_metadata=dict(request_ctx.metadata),
            delete_after_run=delete_after_run,
        )
        return f"Created job '{job.name}' (id: {job.id})"

    def _list_jobs(self) -> dict[str, Any]:
        jobs = self._cron.list_jobs()
        return {
            "count": len(jobs),
            "jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "message": job.message,
                    "session_key": job.session_key,
                    "enabled": job.enabled,
                    "delete_after_run": job.delete_after_run,
                    "schedule": job.schedule.to_dict(),
                    "state": job.state.to_dict(),
                }
                for job in jobs
            ],
        }

    def _remove_job(self, job_id: str | None) -> str:
        if not str(job_id or "").strip():
            return self.error("job_id is required when action='remove'")
        return (
            f"Removed job {job_id}"
            if self._cron.remove_job(str(job_id).strip())
            else f"Job {job_id} not found"
        )

    @staticmethod
    def _is_valid_timezone(tz: str) -> bool:
        try:
            CronTool._zoneinfo(tz)
            return True
        except Exception:
            return False

    @staticmethod
    def _zoneinfo(tz: str):
        if str(tz).strip().upper() == "UTC":
            return timezone.utc
        return ZoneInfo(tz)
