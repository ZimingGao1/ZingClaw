"""Session persistence and history management for ZingClaw."""

from __future__ import annotations

import json
import logging
import os
import re
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_HISTORY_MESSAGES = 120
_PREVIEW_MAX_CHARS = 120
_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")
_UNDERSCORE_RE = re.compile(r"_+")


def _safe_filename_fragment(value: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS_RE.sub("_", value.replace(":", "_"))
    cleaned = _WHITESPACE_RE.sub("_", cleaned)
    cleaned = _UNDERSCORE_RE.sub("_", cleaned).strip("._ ")
    return cleaned or "session"


def _parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    with suppress(ValueError):
        return datetime.fromisoformat(raw)
    return None


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        return " ".join(parts)
    return ""


def _estimate_message_tokens(message: dict[str, Any]) -> int:
    try:
        payload = json.dumps(message, ensure_ascii=False)
    except TypeError:
        payload = str(message)
    return max(1, len(payload) // 4)


def _metadata_title(metadata: Any) -> str:
    if not isinstance(metadata, dict):
        return ""
    title = metadata.get("title")
    return title if isinstance(title, str) else ""


def _message_preview_text(message: dict[str, Any]) -> str:
    text = re.sub(r"\s+", " ", _extract_text_content(message.get("content"))).strip()
    if len(text) > _PREVIEW_MAX_CHARS:
        text = text[: _PREVIEW_MAX_CHARS - 3].rstrip() + "..."
    return text


@dataclass
class Session:
    """One persisted conversation session."""

    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.last_consolidated, bool)
            or not isinstance(self.last_consolidated, int)
            or not 0 <= self.last_consolidated <= len(self.messages)
        ):
            self.last_consolidated = 0

    @staticmethod
    def _annotate_message_time(message: dict[str, Any], content: Any) -> Any:
        timestamp = message.get("timestamp")
        if message.get("role") != "user" or not isinstance(timestamp, str) or not isinstance(content, str):
            return content
        return f"[Message Time: {timestamp}]\n{content}"

    def add_message(self, role: str, content: Any, **kwargs: Any) -> None:
        """Append one message record to the session."""
        self.messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
                **kwargs,
            }
        )
        self.updated_at = datetime.now()

    def get_history(
        self,
        max_messages: int = _DEFAULT_HISTORY_MESSAGES,
        *,
        max_tokens: int = 0,
        include_timestamps: bool = False,
        extend_to_user: bool = False,
    ) -> list[dict[str, Any]]:
        """Return replay-ready history for provider calls."""
        unconsolidated = self.messages[self.last_consolidated :]
        if not unconsolidated:
            return []

        if max_messages <= 0:
            sliced = list(unconsolidated)
        else:
            start = max(0, len(unconsolidated) - max_messages)
            if extend_to_user:
                for index in range(start, -1, -1):
                    if unconsolidated[index].get("role") == "user":
                        start = index
                        break
            sliced = unconsolidated[start:]

        history: list[dict[str, Any]] = []
        for message in sliced:
            role = message.get("role")
            if not isinstance(role, str) or not role:
                continue

            content = deepcopy(message.get("content", ""))
            if include_timestamps:
                content = self._annotate_message_time(message, content)

            entry: dict[str, Any] = {
                "role": role,
                "content": content,
            }
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content", "partial"):
                if key in message:
                    entry[key] = deepcopy(message[key])

            if role == "tool" and entry["content"] is None:
                entry["content"] = ""

            if role == "assistant" and isinstance(entry["content"], str) and not entry["content"].strip():
                if not any(key in entry for key in ("tool_calls", "reasoning_content")):
                    continue

            history.append(entry)

        if max_tokens > 0 and history:
            kept: list[dict[str, Any]] = []
            used = 0
            for message in reversed(history):
                tokens = _estimate_message_tokens(message)
                if kept and used + tokens > max_tokens:
                    break
                kept.append(message)
                used += tokens
            history = list(reversed(kept))

        return history

    def clear(self) -> None:
        """Clear message history while preserving session metadata."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()
        self.metadata.pop("_last_summary", None)


class SessionManager:
    """Manage JSONL-backed sessions stored on local disk."""

    def __init__(self, sessions_dir: Path):
        self.sessions_dir = Path(sessions_dir).expanduser().resolve()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Session] = {}

    @staticmethod
    def safe_key(key: str) -> str:
        return _safe_filename_fragment(key)

    def _get_session_path(self, key: str) -> Path:
        return self.sessions_dir / f"{self.safe_key(key)}.jsonl"

    def get_or_create(self, key: str) -> Session:
        if key in self._cache:
            return self._cache[key]

        session = self._load(key) or Session(key=key)
        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            return self._read_session(path, fallback_key=key)
        except Exception as exc:
            logger.warning("Failed to load session %s: %s", key, exc)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info(
                    "Recovered session %s from partially corrupt JSONL (%s messages)",
                    key,
                    len(repaired.messages),
                )
            return repaired

    def _read_session(
        self,
        path: Path,
        *,
        fallback_key: str,
        repair: bool = False,
    ) -> Session | None:
        messages: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {}
        created_at: datetime | None = None
        updated_at: datetime | None = None
        last_consolidated = 0
        stored_key = fallback_key
        skipped = 0

        with path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    if not repair:
                        raise
                    skipped += 1
                    continue

                if not isinstance(data, dict):
                    if repair:
                        skipped += 1
                        continue
                    raise ValueError("session record must be an object")

                if data.get("_type") == "metadata":
                    stored = data.get("key")
                    if isinstance(stored, str) and stored.strip():
                        stored_key = stored
                    raw_metadata = data.get("metadata")
                    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
                    created_at = _parse_timestamp(data.get("created_at"))
                    updated_at = _parse_timestamp(data.get("updated_at"))
                    raw_last = data.get("last_consolidated", 0)
                    if isinstance(raw_last, int) and not isinstance(raw_last, bool):
                        last_consolidated = raw_last
                    continue

                messages.append(data)

        if repair and skipped:
            logger.warning("Skipped %s corrupt records while repairing %s", skipped, fallback_key)

        if not messages and not metadata and created_at is None and updated_at is None:
            return None

        return Session(
            key=stored_key,
            messages=messages,
            created_at=created_at or datetime.now(),
            updated_at=updated_at or datetime.now(),
            metadata=metadata,
            last_consolidated=last_consolidated,
        )

    def _repair(self, key: str) -> Session | None:
        path = self._get_session_path(key)
        if not path.exists():
            return None
        try:
            return self._read_session(path, fallback_key=key, repair=True)
        except Exception as exc:
            logger.warning("Repair failed for session %s: %s", key, exc)
            return None

    @staticmethod
    def _session_payload(session: Session) -> dict[str, Any]:
        return {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": deepcopy(session.metadata),
            "messages": deepcopy(session.messages),
        }

    def save(self, session: Session, *, fsync: bool = False) -> None:
        """Atomically persist one session to disk."""
        path = self._get_session_path(session.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".jsonl.tmp")

        try:
            with tmp_path.open("w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "_type": "metadata",
                            "key": session.key,
                            "created_at": session.created_at.isoformat(),
                            "updated_at": session.updated_at.isoformat(),
                            "metadata": session.metadata,
                            "last_consolidated": session.last_consolidated,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                for message in session.messages:
                    handle.write(json.dumps(message, ensure_ascii=False) + "\n")

                if fsync:
                    handle.flush()
                    os.fsync(handle.fileno())

            os.replace(tmp_path, path)

            if fsync:
                with suppress(PermissionError):
                    fd = os.open(str(path.parent), os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        self._cache[session.key] = session

    def flush_all(self) -> int:
        """Persist all cached sessions with fsync for clean shutdown."""
        flushed = 0
        for key, session in list(self._cache.items()):
            try:
                self.save(session, fsync=True)
                flushed += 1
            except Exception:
                logger.warning("Failed to flush session %s", key, exc_info=True)
        return flushed

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def delete_session(self, key: str) -> bool:
        path = self._get_session_path(key)
        self.invalidate(key)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Failed to delete session %s: %s", key, exc)
            return False
        return True

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        path = self._get_session_path(key)
        if not path.exists():
            return None
        try:
            session = self._read_session(path, fallback_key=key)
        except Exception as exc:
            logger.warning("Failed to read session %s: %s", key, exc)
            session = self._repair(key)
        return None if session is None else self._session_payload(session)

    def read_session_metadata(self, key: str) -> dict[str, Any] | None:
        payload = self.read_session_file(key)
        if payload is None:
            return None
        return {
            "key": payload["key"],
            "created_at": payload["created_at"],
            "updated_at": payload["updated_at"],
            "metadata": payload["metadata"],
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []

        for path in self.sessions_dir.glob("*.jsonl"):
            fallback_key = path.stem
            try:
                with path.open(encoding="utf-8") as handle:
                    first_line = handle.readline().strip()
                    if not first_line:
                        continue

                    metadata_line = json.loads(first_line)
                    if not isinstance(metadata_line, dict) or metadata_line.get("_type") != "metadata":
                        continue

                    key = metadata_line.get("key")
                    raw_metadata = metadata_line.get("metadata")
                    preview = ""
                    assistant_fallback = ""

                    for raw_line in handle:
                        line = raw_line.strip()
                        if not line:
                            continue
                        item = json.loads(line)
                        if not isinstance(item, dict) or item.get("_type") == "metadata":
                            continue
                        text = _message_preview_text(item)
                        if not text:
                            continue
                        if item.get("role") == "user":
                            preview = text
                            break
                        if not assistant_fallback and item.get("role") == "assistant":
                            assistant_fallback = text

                    sessions.append(
                        {
                            "key": key if isinstance(key, str) and key else fallback_key,
                            "created_at": metadata_line.get("created_at"),
                            "updated_at": metadata_line.get("updated_at"),
                            "title": _metadata_title(raw_metadata),
                            "preview": preview or assistant_fallback,
                            "path": str(path),
                        }
                    )
            except Exception:
                repaired = self._repair(fallback_key)
                if repaired is None:
                    continue
                sessions.append(
                    {
                        "key": repaired.key,
                        "created_at": repaired.created_at.isoformat(),
                        "updated_at": repaired.updated_at.isoformat(),
                        "title": _metadata_title(repaired.metadata),
                        "preview": next(
                            (
                                text
                                for message in repaired.messages
                                if (text := _message_preview_text(message))
                            ),
                            "",
                        ),
                        "path": str(path),
                    }
                )

        return sorted(sessions, key=lambda item: item.get("updated_at") or "", reverse=True)
