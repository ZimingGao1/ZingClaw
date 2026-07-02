"""Spawn tool for background subagent execution."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from zingclaw.tools.base import Tool, tool_parameters
from zingclaw.tools.context import RequestContext

if TYPE_CHECKING:
    from zingclaw.agent.subagent import SubagentManager


class _SpawnContextMixin:
    """Track origin context for spawned background tasks."""

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
            "task": {
                "type": "string",
                "description": "The task for the background subagent to complete.",
                "minLength": 1,
                "maxLength": 20000,
            },
            "label": {
                "type": ["string", "null"],
                "description": "Optional short label for display and later notifications.",
                "maxLength": 120,
            },
            "temperature": {
                "type": ["number", "null"],
                "description": "Optional sampling temperature for the spawned subagent.",
                "minimum": 0.0,
                "maximum": 2.0,
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    }
)
class SpawnTool(Tool, _SpawnContextMixin):
    """Start one background subagent."""

    _scopes = {"core"}

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "subagent_manager", None) is not None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        manager = getattr(ctx, "subagent_manager", None)
        assert manager is not None
        return cls(manager=manager)

    def __init__(self, manager: "SubagentManager") -> None:
        _SpawnContextMixin.__init__(self)
        self._manager = manager

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a background subagent to handle a task independently. "
            "Use this for longer multi-step work that does not need to block the current turn. "
            "The result will be added back into the same session when it finishes."
        )

    async def execute(
        self,
        task: str,
        label: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        request_ctx = self._current_request_context()
        if request_ctx is None or not request_ctx.session_key:
            return self.error("spawn requires an active session context.")
        task = str(task).strip()
        if not task:
            return self.error("task must not be empty")
        return await self._manager.spawn(
            task=task,
            label=label,
            session_key=request_ctx.session_key,
            origin_channel=request_ctx.channel,
            origin_chat_id=request_ctx.chat_id,
            origin_message_id=str(request_ctx.metadata.get("message_id") or "").strip() or None,
            temperature=temperature,
        )


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "all_sessions": {
                "type": ["boolean", "null"],
                "description": "When true, show subagents from every session instead of only the current session.",
            },
            "running_only": {
                "type": ["boolean", "null"],
                "description": "When true, return only currently running subagents.",
            },
        },
        "additionalProperties": False,
    }
)
class ListSubagentsTool(Tool, _SpawnContextMixin):
    """List background subagents."""

    _scopes = {"core"}

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "subagent_manager", None) is not None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        manager = getattr(ctx, "subagent_manager", None)
        assert manager is not None
        return cls(manager=manager)

    def __init__(self, manager: "SubagentManager") -> None:
        _SpawnContextMixin.__init__(self)
        self._manager = manager

    @property
    def name(self) -> str:
        return "list_subagents"

    @property
    def description(self) -> str:
        return (
            "List background subagents, their current phase, and whether they are still running. "
            "Use this to inspect spawned work before deciding whether to wait, continue, or cancel."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        all_sessions: bool | None = None,
        running_only: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        request_ctx = self._current_request_context()
        session_key = None if all_sessions else (None if request_ctx is None else request_ctx.session_key)
        items = self._manager.list_statuses(session_key=session_key)
        if running_only:
            items = [item for item in items if item.get("running")]
        return {
            "count": len(items),
            "subagents": items,
        }


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The subagent task id to cancel.",
                "minLength": 1,
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }
)
class CancelSubagentTool(Tool, _SpawnContextMixin):
    """Cancel one background subagent."""

    _scopes = {"core"}

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return getattr(ctx, "subagent_manager", None) is not None

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        manager = getattr(ctx, "subagent_manager", None)
        assert manager is not None
        return cls(manager=manager)

    def __init__(self, manager: "SubagentManager") -> None:
        _SpawnContextMixin.__init__(self)
        self._manager = manager

    @property
    def name(self) -> str:
        return "cancel_subagent"

    @property
    def description(self) -> str:
        return (
            "Cancel a running background subagent by task_id. "
            "Use list_subagents first if you need to recover the current task ids."
        )

    async def execute(
        self,
        task_id: str,
        **kwargs: Any,
    ) -> str:
        del kwargs
        request_ctx = self._current_request_context()
        return await self._manager.cancel_task(
            task_id.strip(),
            session_key=None if request_ctx is None else request_ctx.session_key,
        )
