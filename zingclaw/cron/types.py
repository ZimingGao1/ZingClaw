"""Cron data models for scheduled reminders and tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class CronSchedule:
    """Schedule definition for one cron job."""

    kind: Literal["at", "every", "cron"]
    at_ms: int | None = None
    every_ms: int | None = None
    expr: str | None = None
    tz: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "at_ms": self.at_ms,
            "every_ms": self.every_ms,
            "expr": self.expr,
            "tz": self.tz,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CronSchedule":
        return cls(
            kind=str(payload.get("kind") or "every"),
            at_ms=_int_or_none(payload.get("at_ms")),
            every_ms=_int_or_none(payload.get("every_ms")),
            expr=_str_or_none(payload.get("expr")),
            tz=_str_or_none(payload.get("tz")),
        )


@dataclass(slots=True)
class CronJobState:
    """Execution state tracked for one cron job."""

    last_run_at_ms: int | None = None
    next_run_at_ms: int | None = None
    last_status: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_run_at_ms": self.last_run_at_ms,
            "next_run_at_ms": self.next_run_at_ms,
            "last_status": self.last_status,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CronJobState":
        return cls(
            last_run_at_ms=_int_or_none(payload.get("last_run_at_ms")),
            next_run_at_ms=_int_or_none(payload.get("next_run_at_ms")),
            last_status=_str_or_none(payload.get("last_status")),
            last_error=_str_or_none(payload.get("last_error")),
        )


@dataclass(slots=True)
class CronJob:
    """One persisted cron job."""

    id: str
    name: str
    message: str
    session_key: str
    origin_channel: str
    origin_chat_id: str
    origin_metadata: dict[str, Any] = field(default_factory=dict)
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    state: CronJobState = field(default_factory=CronJobState)
    delete_after_run: bool = False
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "message": self.message,
            "session_key": self.session_key,
            "origin_channel": self.origin_channel,
            "origin_chat_id": self.origin_chat_id,
            "origin_metadata": dict(self.origin_metadata),
            "schedule": self.schedule.to_dict(),
            "state": self.state.to_dict(),
            "delete_after_run": bool(self.delete_after_run),
            "enabled": bool(self.enabled),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CronJob":
        return cls(
            id=str(payload.get("id") or ""),
            name=str(payload.get("name") or ""),
            message=str(payload.get("message") or ""),
            session_key=str(payload.get("session_key") or ""),
            origin_channel=str(payload.get("origin_channel") or ""),
            origin_chat_id=str(payload.get("origin_chat_id") or ""),
            origin_metadata=dict(payload.get("origin_metadata") or {}),
            schedule=CronSchedule.from_dict(dict(payload.get("schedule") or {})),
            state=CronJobState.from_dict(dict(payload.get("state") or {})),
            delete_after_run=bool(payload.get("delete_after_run", False)),
            enabled=bool(payload.get("enabled", True)),
        )


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
