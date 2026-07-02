"""Virtual long-term memory tool."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Any

from zingclaw.memory import MemoryStore
from zingclaw.tools.base import Tool, tool_parameters
from zingclaw.tools.context import RequestContext


class _MemoryToolMixin:
    """Shared request context plumbing for virtual memory tools."""

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
            "content": {
                "type": "string",
                "description": (
                    "One durable long-term memory note to append to MEMORY.md. "
                    "Use this for stable user preferences, important project facts, "
                    "or decisions worth remembering across turns."
                ),
                "minLength": 1,
                "maxLength": 12000,
            }
        },
        "required": ["content"],
        "additionalProperties": False,
    }
)
class SaveMemoryTool(Tool, _MemoryToolMixin):
    """Append one durable note into long-term memory."""

    _scopes = {"core", "subagent"}

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        settings = getattr(ctx, "settings", None)
        if settings is None:
            return True
        return bool(getattr(settings.memory, "enabled", True))

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        settings = getattr(ctx, "settings", None)
        if settings is not None:
            memory_dir = settings.memory_dir
            max_history_entries = settings.memory.max_history_entries
        else:
            memory_dir = Path(ctx.workspace) / "memory"
            max_history_entries = 100
        return cls(
            store=MemoryStore(memory_dir, max_history_entries=max_history_entries),
        )

    def __init__(self, *, store: MemoryStore) -> None:
        _MemoryToolMixin.__init__(self)
        self._store = store

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Append a durable long-term note to MEMORY.md. "
            "Use this only for facts worth remembering across future turns, "
            "such as user preferences, project constraints, or important decisions."
        )

    async def execute(self, content: str, **kwargs: Any) -> str:
        del kwargs
        note = str(content or "").strip()
        if not note:
            return "Error: content must not be empty."
        self._store.save_memory(note)
        request_ctx = self._current_request_context()
        session_suffix = ""
        if request_ctx is not None and request_ctx.session_key:
            session_suffix = f" for session {request_ctx.session_key}"
        return f"Saved to long-term memory{session_suffix}."
