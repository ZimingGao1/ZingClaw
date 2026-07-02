"""Idle-session auto compaction aligned with ZingClaw's memory layer."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Coroutine

if TYPE_CHECKING:
    from zingclaw.memory import Consolidator
    from zingclaw.session import Session, SessionManager


class AutoCompact:
    """Schedule or inline-compact idle sessions."""

    _RECENT_SUFFIX_MESSAGES = 8
    _INTERNAL_SESSION_PREFIXES = ("dream:",)

    def __init__(
        self,
        sessions: "SessionManager",
        consolidator: "Consolidator",
        *,
        session_ttl_minutes: int = 0,
    ) -> None:
        self.sessions = sessions
        self.consolidator = consolidator
        self._ttl = max(0, int(session_ttl_minutes))
        self._archiving: set[str] = set()
        self._summaries: dict[str, tuple[str, datetime]] = {}

    def _is_expired(
        self,
        ts: datetime | str | None,
        now: datetime | None = None,
    ) -> bool:
        if self._ttl <= 0 or not ts:
            return False
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return ((now or datetime.now()) - ts).total_seconds() >= self._ttl * 60

    @staticmethod
    def _format_summary(text: str, last_active: datetime) -> str:
        return f"Previous conversation summary (last active {last_active.isoformat()}):\n{text}"

    @classmethod
    def _is_internal_session(cls, key: str) -> bool:
        return key.startswith(cls._INTERNAL_SESSION_PREFIXES)

    @staticmethod
    def _session_timestamp(info: dict[str, object]) -> datetime | str | None:
        last_active = info.get("last_active_at")
        if isinstance(last_active, str) and last_active.strip():
            return last_active
        updated = info.get("updated_at")
        if isinstance(updated, str) and updated.strip():
            return updated
        return None

    def check_expired(
        self,
        schedule_background: Callable[[Coroutine], None],
        active_session_keys: Collection[str] = (),
    ) -> None:
        """Schedule archival for idle sessions not currently in use."""
        now = datetime.now()
        for info in self.sessions.list_sessions():
            key = str(info.get("key") or "").strip()
            if not key or self._is_internal_session(key) or key in self._archiving:
                continue
            if key in active_session_keys:
                continue
            if self._is_expired(self._session_timestamp(info), now):
                self._archiving.add(key)
                schedule_background(self._archive(key))

    async def _archive(self, key: str) -> None:
        if self._is_internal_session(key):
            self._archiving.discard(key)
            return
        try:
            summary = await self.consolidator.compact_idle_session(
                key,
                max_suffix=self._RECENT_SUFFIX_MESSAGES,
            )
            if summary and summary != "(nothing)":
                session = self.sessions.get_or_create(key)
                meta = session.metadata.get("_last_summary")
                if isinstance(meta, dict):
                    raw_last_active = meta.get("last_active") or meta.get("compacted_at")
                    if isinstance(raw_last_active, str) and raw_last_active.strip():
                        self._summaries[key] = (
                            str(meta.get("text") or summary),
                            datetime.fromisoformat(raw_last_active),
                        )
        finally:
            self._archiving.discard(key)

    async def prepare_session(self, session: "Session", key: str) -> tuple["Session", str | None]:
        """Ensure an expired current session is compacted before the next active turn."""
        if self._is_internal_session(key):
            self._archiving.discard(key)
            self._summaries.pop(key, None)
            return session, None

        if key in self._archiving:
            self.sessions.invalidate(key)
            session = self.sessions.get_or_create(key)
        elif self._is_expired(session.updated_at):
            self._archiving.add(key)
            await self._archive(key)
            self.sessions.invalidate(key)
            session = self.sessions.get_or_create(key)

        entry = self._summaries.pop(key, None)
        if entry:
            return session, self._format_summary(entry[0], entry[1])

        meta = session.metadata.get("_last_summary")
        if isinstance(meta, dict):
            text = meta.get("text")
            raw_last_active = meta.get("last_active") or meta.get("compacted_at")
            if isinstance(text, str) and text.strip() and isinstance(raw_last_active, str) and raw_last_active.strip():
                return session, self._format_summary(text, datetime.fromisoformat(raw_last_active))
        return session, None
