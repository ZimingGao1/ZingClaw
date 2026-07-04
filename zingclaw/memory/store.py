"""Long-term memory store and history consolidation for ZingClaw."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from zingclaw.providers import LLMProvider
from zingclaw.session import Session, SessionManager

logger = logging.getLogger(__name__)

_HISTORY_ENTRY_HARD_CAP = 64_000
_RAW_ARCHIVE_MAX_CHARS = 16_000
_ARCHIVE_SUMMARY_MAX_CHARS = 8_000
_RECENT_HISTORY_PROMPT_ENTRIES = 5
_RECENT_HISTORY_PROMPT_CHARS = 2_500
_DREAM_HISTORY_ENTRY_CHARS = 1_000
_MIN_PROMPT_TOKEN_BUDGET = 2_000


def _estimate_tokens(value: Any) -> int:
    try:
        payload = json.dumps(value, ensure_ascii=False)
    except TypeError:
        payload = str(value)
    return max(1, len(payload) // 4)


def _truncate_text(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    omitted = len(text) - limit
    half = max(1, limit // 2)
    return (
        text[:half]
        + f"\n\n... ({omitted:,} chars truncated) ...\n\n"
        + text[-half:]
    )


def _session_summary_text(session: Session) -> str | None:
    raw = session.metadata.get("_last_summary")
    if isinstance(raw, dict):
        text = raw.get("text")
        return text.strip() if isinstance(text, str) and text.strip() else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


class MemoryStore:
    """File-backed long-term memory and archived history store."""

    def __init__(self, memory_dir: Path, *, max_history_entries: int = 100) -> None:
        self.memory_dir = Path(memory_dir).expanduser().resolve()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.max_history_entries = max(0, int(max_history_entries))
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "history.jsonl"
        self.history_markdown_file = self.memory_dir / "HISTORY.md"
        self.cursor_file = self.memory_dir / ".cursor"
        self.dream_cursor_file = self.memory_dir / ".dream_cursor"
        self._append_lock = threading.Lock()
        self._maybe_sync_history_markdown()

    @staticmethod
    def _read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def read_memory(self) -> str:
        return self._read_file(self.memory_file)

    def write_memory(self, content: str) -> None:
        text = content.rstrip()
        self.memory_file.write_text((text + "\n") if text else "", encoding="utf-8")

    def save_memory(self, content: str) -> None:
        """Append one durable memory note with a timestamp heading."""
        note = content.strip()
        if not note:
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        existing = self.read_memory().rstrip()
        block = f"## {timestamp}\n{note}"
        payload = f"{existing}\n\n{block}\n" if existing else f"{block}\n"
        self.memory_file.write_text(payload, encoding="utf-8")

    def get_memory_context(self) -> str:
        long_term = self.read_memory().strip()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    @staticmethod
    def _valid_cursor(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    def _read_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        with suppress(FileNotFoundError):
            with self.history_file.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    with suppress(json.JSONDecodeError):
                        item = json.loads(line)
                        if isinstance(item, dict):
                            entries.append(item)
        return entries

    @classmethod
    def _valid_history_payload(cls, entry: dict[str, Any]) -> bool:
        if cls._valid_cursor(entry.get("cursor")) is None:
            return False
        if not isinstance(entry.get("timestamp"), str):
            return False
        if not isinstance(entry.get("content"), str):
            return False
        session_key = entry.get("session_key")
        return session_key is None or isinstance(session_key, str)

    def _iter_valid_entries(self) -> list[dict[str, Any]]:
        return [entry for entry in self._read_entries() if self._valid_history_payload(entry)]

    def _read_last_entry(self) -> dict[str, Any] | None:
        try:
            with self.history_file.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                if size <= 0:
                    return None
                read_size = min(size, 4096)
                handle.seek(size - read_size)
                lines = [line for line in handle.read().decode("utf-8").splitlines() if line.strip()]
                if not lines:
                    return None
                entry = json.loads(lines[-1])
                return entry if isinstance(entry, dict) else None
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _read_cursor_counter(self) -> int | None:
        with suppress(FileNotFoundError, ValueError):
            value = int(self.cursor_file.read_text(encoding="utf-8").strip())
            if value >= 0:
                return value
        return None

    def _next_cursor(self) -> int:
        counter = self._read_cursor_counter()
        last = self._read_last_entry() or {}
        last_cursor = self._valid_cursor(last.get("cursor"))
        max_history_cursor = max(
            (self._valid_cursor(entry.get("cursor")) or 0 for entry in self._iter_valid_entries()),
            default=0,
        )
        if counter is not None and last_cursor is not None:
            return max(counter, last_cursor) + 1
        if counter is not None:
            return max(counter, max_history_cursor) + 1
        if last_cursor is not None:
            return last_cursor + 1
        return max_history_cursor + 1

    def get_last_dream_cursor(self) -> int:
        with suppress(FileNotFoundError, OSError, ValueError):
            value = int(self.dream_cursor_file.read_text(encoding="utf-8").strip())
            if value >= 0:
                return value
        return 0

    def set_last_dream_cursor(self, cursor: int) -> None:
        self.dream_cursor_file.write_text(str(max(0, int(cursor))), encoding="utf-8")

    def build_dream_prompt(self, *, max_entries: int = 20) -> tuple[str, int, int] | None:
        """Build a prompt that asks an LLM to extract durable memory from archived history.

        Returns ``(prompt, last_cursor, entry_count)`` or ``None`` when every
        archived history entry has already been processed by Dream.
        """
        last_cursor = self.get_last_dream_cursor()
        entries = self.read_unprocessed_history(since_cursor=last_cursor)
        if not entries:
            return None

        batch = entries[: max(1, int(max_entries))]
        history_text = "\n\n".join(
            self._format_dream_history_entry(entry) for entry in batch
        )
        prompt = (
            "You are ZingClaw's long-term memory consolidation engine.\n"
            "Read the archived conversation summaries below and extract only durable, useful memory.\n\n"
            "Write concise bullet points for facts that should remain useful across future sessions:\n"
            "- user preferences\n"
            "- stable project facts\n"
            "- important decisions\n"
            "- recurring constraints or safety requirements\n"
            "- lessons learned from previous work\n\n"
            "Ignore one-off chatter, temporary command output, and details that are only useful inside one turn.\n"
            "If there is nothing worth preserving, reply exactly: (nothing)\n\n"
            "## Archived History\n"
            f"{history_text}"
        )
        cursor = self._valid_cursor(batch[-1].get("cursor")) or last_cursor
        return prompt, cursor, len(batch)

    @staticmethod
    def _format_dream_history_entry(entry: dict[str, Any]) -> str:
        cursor = entry.get("cursor")
        timestamp = str(entry.get("timestamp") or "unknown time")
        session_key = str(entry.get("session_key") or "").strip()
        content = _truncate_text(str(entry.get("content") or "").strip(), _DREAM_HISTORY_ENTRY_CHARS)
        session_line = f"\nSession: {session_key}" if session_key else ""
        return f"### Cursor {cursor} [{timestamp}]{session_line}\n{content}"

    def append_history(
        self,
        entry: str,
        *,
        max_chars: int | None = None,
        session_key: str | None = None,
    ) -> int:
        """Append one archived history entry and return its cursor."""
        limit = max_chars if max_chars is not None else _HISTORY_ENTRY_HARD_CAP
        raw = entry.rstrip()
        if len(raw) > limit:
            raw = _truncate_text(raw, limit)

        with self._append_lock:
            cursor = self._next_cursor()
            record: dict[str, Any] = {
                "cursor": cursor,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "content": raw,
            }
            if session_key:
                record["session_key"] = session_key
            with self.history_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.cursor_file.write_text(str(cursor), encoding="utf-8")
            self._sync_history_markdown_locked()
        return cursor

    def raw_archive(self, messages: list[dict[str, Any]], *, session_key: str | None = None) -> int:
        """Fallback archive path when LLM summarization is unavailable."""
        return self.append_history(
            self.format_messages(messages),
            max_chars=_RAW_ARCHIVE_MAX_CHARS,
            session_key=session_key,
        )

    def read_unprocessed_history(self, *, since_cursor: int = 0) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self._iter_valid_entries()
            if (self._valid_cursor(entry.get("cursor")) or 0) > since_cursor
        ]

    def read_history_entries(
        self,
        *,
        session_key: str | None = None,
        since_cursor: int | None = None,
    ) -> list[dict[str, Any]]:
        entries = self.read_unprocessed_history(since_cursor=since_cursor or 0)
        if session_key:
            entries = [entry for entry in entries if entry.get("session_key") == session_key]
        return entries

    def read_recent_history_for_prompt(
        self,
        *,
        session_key: str | None = None,
        since_cursor: int | None = None,
        limit: int = _RECENT_HISTORY_PROMPT_ENTRIES,
        max_chars: int = _RECENT_HISTORY_PROMPT_CHARS,
    ) -> list[dict[str, Any]]:
        entries = self.read_history_entries(session_key=session_key, since_cursor=since_cursor)
        if limit > 0:
            entries = entries[-limit:]
        if max_chars > 0 and entries:
            kept: list[dict[str, Any]] = []
            used = 0
            for entry in reversed(entries):
                text = str(entry.get("content") or "")
                cost = len(text)
                if kept and used + cost > max_chars:
                    break
                kept.append(entry)
                used += cost
            entries = list(reversed(kept))
        return entries

    def render_recent_history_section(
        self,
        *,
        session_key: str | None = None,
        since_cursor: int | None = None,
        limit: int = _RECENT_HISTORY_PROMPT_ENTRIES,
        max_chars: int = _RECENT_HISTORY_PROMPT_CHARS,
    ) -> str:
        entries = self.read_recent_history_for_prompt(
            session_key=session_key,
            since_cursor=since_cursor,
            limit=limit,
            max_chars=max_chars,
        )
        if not entries:
            return ""
        lines = ["# Recent Archived History"]
        for entry in entries:
            timestamp = str(entry.get("timestamp") or "").strip()
            content = str(entry.get("content") or "").strip()
            if not content:
                continue
            prefix = f"- [{timestamp}] " if timestamp else "- "
            lines.append(prefix + content)
        return "\n".join(lines) if len(lines) > 1 else ""

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        tmp_path = self.history_file.with_suffix(".jsonl.tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as handle:
                for entry in entries:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.history_file)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _render_history_markdown(self, entries: list[dict[str, Any]]) -> str:
        if not entries:
            return ""

        blocks: list[str] = []
        for entry in entries:
            timestamp = str(entry.get("timestamp") or "").strip() or "unknown time"
            content = str(entry.get("content") or "").strip()
            session_key = str(entry.get("session_key") or "").strip()
            lines = [f"### {timestamp}"]
            if session_key:
                lines.append(f"Session: `{session_key}`")
            if content:
                lines.extend(["", content])
            blocks.append("\n".join(lines).rstrip())
        return "\n\n---\n\n".join(blocks) + "\n"

    def _write_history_markdown(self, entries: list[dict[str, Any]]) -> None:
        self.history_markdown_file.write_text(
            self._render_history_markdown(entries),
            encoding="utf-8",
        )

    def _sync_history_markdown_locked(self) -> None:
        entries = self._iter_valid_entries()
        if self.max_history_entries > 0 and len(entries) > self.max_history_entries:
            entries = entries[-self.max_history_entries :]
            self._write_entries(entries)
        self._write_history_markdown(entries)

    def _maybe_sync_history_markdown(self) -> None:
        if not self.history_file.exists():
            return
        if self.history_markdown_file.exists():
            try:
                if self.history_markdown_file.stat().st_mtime >= self.history_file.stat().st_mtime:
                    return
            except OSError:
                pass
        with self._append_lock:
            self._sync_history_markdown_locked()

    def compact_history(self) -> None:
        with self._append_lock:
            self._sync_history_markdown_locked()

    @staticmethod
    def format_messages(messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages:
            role = str(message.get("role") or "unknown").upper()
            timestamp = str(message.get("timestamp") or "").strip()
            content = message.get("content")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str):
                            text_parts.append(text)
                text = " ".join(text_parts).strip()
            else:
                text = str(content or "").strip()
            if not text and role == "ASSISTANT" and message.get("tool_calls"):
                text = "[assistant issued tool calls]"
            if not text and role == "TOOL":
                text = "[tool returned no textual output]"
            suffix = f"[{timestamp}] " if timestamp else ""
            if role == "TOOL":
                name = str(message.get("name") or "tool").strip() or "tool"
                lines.append(f"{suffix}TOOL({name}): {text}")
            else:
                lines.append(f"{suffix}{role}: {text}")
        return "\n".join(line for line in lines if line.strip())


class Consolidator:
    """Summarize old session turns into archived history entries."""

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        *,
        model: str,
        sessions: SessionManager,
        consolidation_threshold: int = 50,
        recent_keep_messages: int | None = None,
        prompt_token_budget: int | None = None,
    ) -> None:
        self.store = store
        self.provider = provider
        self.model = model
        self.sessions = sessions
        self.consolidation_threshold = max(0, int(consolidation_threshold))
        if recent_keep_messages is None:
            derived_keep = max(2, min(10, max(1, self.consolidation_threshold // 2)))
            self.recent_keep_messages = derived_keep
        else:
            self.recent_keep_messages = max(1, int(recent_keep_messages))
        self.prompt_token_budget = (
            max(_MIN_PROMPT_TOKEN_BUDGET, int(prompt_token_budget))
            if prompt_token_budget is not None
            else max(_MIN_PROMPT_TOKEN_BUDGET, provider.generation.max_tokens * 3)
        )
        self._locks: dict[str, asyncio.Lock] = {}

    def get_lock(self, session_key: str) -> asyncio.Lock:
        lock = self._locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_key] = lock
        return lock

    def _estimate_session_tokens(self, session: Session) -> int:
        history = session.get_history(
            max_messages=0,
            include_timestamps=True,
            extend_to_user=True,
        )
        summary = _session_summary_text(session)
        total = sum(_estimate_tokens(message) for message in history)
        if summary:
            total += _estimate_tokens(summary)
        return total

    def _should_consolidate(self, session: Session) -> bool:
        unconsolidated = len(session.messages) - session.last_consolidated
        if unconsolidated <= self.recent_keep_messages:
            return False
        if self.consolidation_threshold > 0 and unconsolidated >= self.consolidation_threshold:
            return True
        return self._estimate_session_tokens(session) >= self.prompt_token_budget

    def _pick_boundary(self, session: Session) -> int | None:
        start = session.last_consolidated
        tail = session.messages[start:]
        if len(tail) <= self.recent_keep_messages:
            return None

        candidate = max(1, len(tail) - self.recent_keep_messages)
        for rel_idx in range(candidate, len(tail)):
            if tail[rel_idx].get("role") == "user":
                return start + rel_idx
        return None

    async def archive(
        self,
        messages: list[dict[str, Any]],
        *,
        session_key: str | None = None,
        summary_messages: list[dict[str, Any]] | None = None,
    ) -> str | None:
        if not messages:
            return None

        messages_to_summarize = summary_messages if summary_messages is not None else messages
        formatted = MemoryStore.format_messages(messages_to_summarize)
        formatted = _truncate_text(formatted, _RAW_ARCHIVE_MAX_CHARS)
        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a memory consolidation engine. Summarize the archived "
                            "conversation history into concise durable notes. Preserve user goals, "
                            "important decisions, outcomes, and noteworthy tool usage. Reply with "
                            "plain text bullet points only."
                        ),
                    },
                    {"role": "user", "content": formatted},
                ],
                tools=None,
                tool_choice=None,
                max_tokens=min(1024, self.provider.generation.max_tokens),
                temperature=0.2,
                reasoning_effort=None,
            )
            if response.finish_reason == "error":
                raise RuntimeError(response.content or "LLM consolidation failed")
            summary = (response.content or "").strip() or "[no summary]"
            self.store.append_history(
                summary,
                max_chars=_ARCHIVE_SUMMARY_MAX_CHARS,
                session_key=session_key,
            )
            return summary
        except Exception:
            logger.warning("Memory consolidation failed; raw-archiving evicted messages", exc_info=True)
            self.store.raw_archive(messages, session_key=session_key)
            return None

    async def maybe_consolidate(self, session: Session) -> str | None:
        lock = self.get_lock(session.key)
        async with lock:
            session = self.sessions.get_or_create(session.key)
            if not self._should_consolidate(session):
                return None

            boundary = self._pick_boundary(session)
            if boundary is None or boundary <= session.last_consolidated:
                return None

            chunk = deepcopy(session.messages[session.last_consolidated : boundary])
            if not chunk:
                return None

            summary = await self.archive(chunk, session_key=session.key)
            session.last_consolidated = boundary
            if summary is not None:
                session.mark_compacted(summary, trigger="auto")
            else:
                session.mark_compacted(
                    "Older turns were archived to memory/HISTORY.md after summarization fell back to raw archival.",
                    trigger="raw_archive",
                )
            self.sessions.save(session)
            return summary


    async def compact_idle_session(
        self,
        session_key: str,
        *,
        max_suffix: int = 8,
    ) -> str | None:
        """Archive the old prefix of a session, keeping only a recent legal suffix."""
        lock = self.get_lock(session_key)
        async with lock:
            session = self.sessions.get_or_create(session_key)
            messages_to_summarize = list(session.messages[session.last_consolidated :])
            if not messages_to_summarize:
                return ""

            probe = Session(
                key=session.key,
                messages=messages_to_summarize.copy(),
                created_at=session.created_at,
                updated_at=session.updated_at,
                metadata={},
                last_consolidated=0,
            )
            dropped, already_consolidated = probe.retain_recent_legal_suffix(
                max_suffix,
                extend_to_user=True,
            )
            messages_to_keep = probe.messages
            messages_to_remove = dropped[already_consolidated:]

            if not messages_to_remove:
                return ""

            summary = await self.archive(
                messages_to_remove,
                session_key=session_key,
                summary_messages=messages_to_summarize,
            )
            if summary is not None:
                session.mark_compacted(summary, trigger="idle")
            else:
                session.mark_compacted(
                    "Older turns were archived to memory/HISTORY.md after idle compaction fell back to raw archival.",
                    trigger="idle_raw_archive",
                )
            session.messages = messages_to_keep
            session.last_consolidated = 0
            self.sessions.save(session)
            return summary


@dataclass(slots=True)
class DreamResult:
    """Outcome of one Dream long-term memory extraction run."""

    processed_entries: int
    last_cursor: int
    memory_updated: bool
    content: str = ""


class DreamConsolidator:
    """Extract durable long-term memory from archived history entries."""

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        *,
        model: str,
        max_entries: int = 20,
    ) -> None:
        self.store = store
        self.provider = provider
        self.model = model
        self.max_entries = max(1, int(max_entries))

    async def run_once(self) -> DreamResult:
        """Process one batch of archived history into ``MEMORY.md``."""
        built = self.store.build_dream_prompt(max_entries=self.max_entries)
        if built is None:
            return DreamResult(
                processed_entries=0,
                last_cursor=self.store.get_last_dream_cursor(),
                memory_updated=False,
                content="",
            )

        prompt, last_cursor, entry_count = built
        response = await self.provider.chat_with_retry(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You update durable long-term memory. Reply with concise bullet points only, "
                        "or exactly '(nothing)' if nothing should be kept."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            tools=None,
            tool_choice=None,
            max_tokens=min(1024, self.provider.generation.max_tokens),
            temperature=0.2,
            reasoning_effort=None,
        )
        if response.finish_reason == "error":
            raise RuntimeError(response.content or "Dream memory extraction failed")

        content = (response.content or "").strip()
        memory_updated = bool(content and content != "(nothing)")
        if memory_updated:
            self.store.save_memory(
                f"Dream update from archived history through cursor {last_cursor}:\n{content}"
            )
        self.store.set_last_dream_cursor(last_cursor)
        return DreamResult(
            processed_entries=entry_count,
            last_cursor=last_cursor,
            memory_updated=memory_updated,
            content=content,
        )
