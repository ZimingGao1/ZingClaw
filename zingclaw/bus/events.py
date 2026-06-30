"""Standard message-bus event models shared by channels and the agent loop."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_required_text(name: str, value: Any) -> str:
    text = _normalize_optional_text(value)
    if text is None:
        raise ValueError(f"{name} is required")
    return text


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("metadata must be a dict")
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        try:
            normalized[key] = deepcopy(item)
        except Exception:
            # Internal runtime-control payloads may carry live objects such as
            # Future instances that are intentionally passed by reference.
            normalized[key] = item
    return normalized


def _normalize_mapping_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("attachments must be a list")

    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("attachment items must be dict objects")
        normalized.append(deepcopy(item))
    return normalized


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("extra_runtime_lines must be a list")
    return [line for item in value if (line := _normalize_optional_text(item)) is not None]


@dataclass(slots=True)
class InboundMessage:
    """Canonical inbound message sent from a channel into the agent runtime."""

    channel: str
    chat_id: str
    session_key: str
    text: str
    message_id: str | None = None
    sender_id: str | None = None
    thread_id: str | None = None
    current_role: str = "user"
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    extra_runtime_lines: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        self.channel = _normalize_required_text("channel", self.channel)
        self.chat_id = _normalize_required_text("chat_id", self.chat_id)
        self.session_key = _normalize_required_text("session_key", self.session_key)
        self.text = "" if self.text is None else str(self.text)
        self.message_id = _normalize_optional_text(self.message_id)
        self.sender_id = _normalize_optional_text(self.sender_id)
        self.thread_id = _normalize_optional_text(self.thread_id)
        self.current_role = _normalize_required_text("current_role", self.current_role)
        self.attachments = _normalize_mapping_list(self.attachments)
        self.metadata = _normalize_mapping(self.metadata)
        self.extra_runtime_lines = _normalize_text_list(self.extra_runtime_lines)
        if self.raw_payload is not None and not isinstance(self.raw_payload, dict):
            raise TypeError("raw_payload must be a dict or None")
        if self.raw_payload is not None:
            self.raw_payload = deepcopy(self.raw_payload)

    @property
    def kind(self) -> str:
        return "inbound"

    @property
    def has_attachments(self) -> bool:
        return bool(self.attachments)

    def metadata_payload(self) -> dict[str, Any]:
        """Return the metadata shape expected by the current AgentLoop bridge."""
        payload = deepcopy(self.metadata)
        if self.message_id is not None:
            payload["message_id"] = self.message_id
        if self.sender_id is not None:
            payload["sender_id"] = self.sender_id
        if self.thread_id is not None:
            payload["thread_id"] = self.thread_id
        if self.attachments:
            payload["attachments"] = deepcopy(self.attachments)
        return payload

    def to_loop_kwargs(self) -> dict[str, Any]:
        """Bridge this event into the current pre-bus AgentLoop API."""
        return {
            "message": self.text,
            "session_key": self.session_key,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "metadata": self.metadata_payload(),
            "current_role": self.current_role,
            "extra_runtime_lines": list(self.extra_runtime_lines),
        }

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "session_key": self.session_key,
            "text": self.text,
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "thread_id": self.thread_id,
            "current_role": self.current_role,
            "attachments": deepcopy(self.attachments),
            "metadata": deepcopy(self.metadata),
            "extra_runtime_lines": list(self.extra_runtime_lines),
            "created_at": self.created_at.isoformat(),
        }
        if self.raw_payload is not None:
            payload["raw_payload"] = deepcopy(self.raw_payload)
        return payload


@dataclass(slots=True)
class OutboundMessage:
    """Canonical outbound message delivered from the runtime back to a channel."""

    channel: str
    chat_id: str
    session_key: str
    text: str
    reply_to: str | None = None
    message_id: str | None = None
    final: bool = True
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        self.channel = _normalize_required_text("channel", self.channel)
        self.chat_id = _normalize_required_text("chat_id", self.chat_id)
        self.session_key = _normalize_required_text("session_key", self.session_key)
        self.text = "" if self.text is None else str(self.text)
        self.reply_to = _normalize_optional_text(self.reply_to)
        self.message_id = _normalize_optional_text(self.message_id)
        self.attachments = _normalize_mapping_list(self.attachments)
        self.metadata = _normalize_mapping(self.metadata)
        if self.raw_payload is not None and not isinstance(self.raw_payload, dict):
            raise TypeError("raw_payload must be a dict or None")
        if self.raw_payload is not None:
            self.raw_payload = deepcopy(self.raw_payload)

    @property
    def kind(self) -> str:
        return "outbound"

    @property
    def has_attachments(self) -> bool:
        return bool(self.attachments)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "session_key": self.session_key,
            "text": self.text,
            "reply_to": self.reply_to,
            "message_id": self.message_id,
            "final": self.final,
            "attachments": deepcopy(self.attachments),
            "metadata": deepcopy(self.metadata),
            "created_at": self.created_at.isoformat(),
        }
        if self.raw_payload is not None:
            payload["raw_payload"] = deepcopy(self.raw_payload)
        return payload


BusEvent = InboundMessage | OutboundMessage
