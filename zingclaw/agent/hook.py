"""Lifecycle hooks for agent runner integrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentHookContext:
    """Mutable per-iteration state exposed to runner hooks."""

    iteration: int
    messages: list[dict[str, Any]]
    session_key: str | None = None
    channel: str | None = None
    chat_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    response: Any | None = None
    usage: dict[str, int] = field(default_factory=dict)
    tool_calls: list[Any] = field(default_factory=list)
    tool_results: list[str] = field(default_factory=list)
    final_content: str | None = None
    stop_reason: str | None = None
    error: str | None = None


@dataclass(slots=True)
class AgentRunHookContext:
    """Run-level state snapshot exposed to runner hooks."""

    messages: list[dict[str, Any]]
    session_key: str | None = None
    channel: str | None = None
    chat_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    final_content: str | None = None
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    error: str | None = None
    exception: BaseException | None = None


class AgentHook:
    """Minimal lifecycle surface for runner integrations."""

    def wants_streaming(self) -> bool:
        return False

    async def before_run(self, context: AgentRunHookContext) -> None:
        pass

    async def after_run(self, context: AgentRunHookContext) -> None:
        pass

    async def on_error(self, context: AgentRunHookContext) -> None:
        pass

    async def on_finally(self, context: AgentRunHookContext) -> None:
        pass

    async def before_iteration(self, context: AgentHookContext) -> None:
        pass

    async def on_text_delta(self, context: AgentHookContext, delta: str) -> None:
        pass

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        pass

    async def after_iteration(self, context: AgentHookContext) -> None:
        pass


class CompositeHook(AgentHook):
    """Fan out lifecycle notifications to an ordered hook list."""

    def __init__(self, hooks: list[AgentHook] | None = None) -> None:
        self._hooks = list(hooks or [])

    def wants_streaming(self) -> bool:
        return any(hook.wants_streaming() for hook in self._hooks)

    async def before_run(self, context: AgentRunHookContext) -> None:
        for hook in self._hooks:
            await hook.before_run(context)

    async def after_run(self, context: AgentRunHookContext) -> None:
        for hook in self._hooks:
            await hook.after_run(context)

    async def on_error(self, context: AgentRunHookContext) -> None:
        for hook in self._hooks:
            await hook.on_error(context)

    async def on_finally(self, context: AgentRunHookContext) -> None:
        for hook in self._hooks:
            await hook.on_finally(context)

    async def before_iteration(self, context: AgentHookContext) -> None:
        for hook in self._hooks:
            await hook.before_iteration(context)

    async def on_text_delta(self, context: AgentHookContext, delta: str) -> None:
        for hook in self._hooks:
            await hook.on_text_delta(context, delta)

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for hook in self._hooks:
            await hook.before_execute_tools(context)

    async def after_iteration(self, context: AgentHookContext) -> None:
        for hook in self._hooks:
            await hook.after_iteration(context)
