"""Shared session-facing service used by WebUI HTTP and WebSocket handlers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from zingclaw.gateway import GatewayRuntime
from zingclaw.webui.common import (
    session_messages_or_404,
    session_metadata_or_404,
    sorted_sessions,
    websocket_session_key,
)


class SessionNotFoundError(LookupError):
    """Raised when a requested session does not exist."""


@dataclass(slots=True)
class SessionService:
    """Wrap session CRUD and message submission behind one reusable facade."""

    runtime: GatewayRuntime

    def list_sessions(self) -> list[dict[str, Any]]:
        return sorted_sessions(self.runtime)

    def get_metadata(self, key: str) -> dict[str, Any]:
        payload = session_metadata_or_404(self.runtime, key)
        if payload is None:
            raise SessionNotFoundError(key)
        return payload

    def get_messages(self, key: str) -> dict[str, Any]:
        payload = session_messages_or_404(self.runtime, key)
        if payload is None:
            raise SessionNotFoundError(key)
        return payload

    def get_detail(self, key: str) -> dict[str, Any]:
        messages = self.get_messages(key)
        return {
            "session": self.get_metadata(key),
            "messages": messages["messages"],
        }

    def create_session(self, *, key: str = "", title: str = "") -> dict[str, Any]:
        session_key = key.strip() or websocket_session_key(uuid.uuid4().hex[:12])
        session = self.runtime.loop.sessions.get_or_create(session_key)
        if title.strip():
            session.set_title(title.strip(), user_edited=True)
        self.runtime.loop.sessions.save(session)
        return self.get_metadata(session_key)

    def ensure_session(self, key: str) -> dict[str, Any]:
        metadata = session_metadata_or_404(self.runtime, key)
        if metadata is not None:
            return metadata
        session = self.runtime.loop.sessions.get_or_create(key)
        self.runtime.loop.sessions.save(session)
        return self.get_metadata(key)

    def rename_session(self, key: str, title: str) -> dict[str, Any]:
        value = title.strip()
        if not value:
            raise ValueError("title must not be empty")
        session = self.runtime.loop.sessions.rename_session(key, value, user_edited=True)
        return self.get_metadata(session.key)

    def clear_session(self, key: str) -> dict[str, Any]:
        self.get_messages(key)
        session = self.runtime.loop.sessions.get_or_create(key)
        session.clear()
        self.runtime.loop.sessions.save(session)
        return self.get_metadata(key)

    def delete_session(self, key: str) -> None:
        if not self.runtime.loop.sessions.delete_session(key):
            raise SessionNotFoundError(key)

    async def submit_user_message(
        self,
        *,
        text: Any,
        session_key: str,
        chat_id: str,
        metadata: Any = None,
        stream: bool = True,
        message_id: str | None = None,
    ) -> str:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("user_message.text must be a non-empty string")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("user_message.metadata must be an object")
        return await self.runtime.submit_user_message(
            text=text,
            session_key=session_key,
            channel="websocket",
            chat_id=chat_id,
            metadata=dict(metadata or {}),
            wants_stream=bool(stream),
            message_id=message_id or uuid.uuid4().hex,
        )


__all__ = ["SessionNotFoundError", "SessionService"]
