"""Runtime event bus for agent state notifications."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from zingclaw.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext


@dataclass(frozen=True, slots=True)
class RuntimeEventContext:
    """Routing context shared by turn-scoped runtime events."""

    channel: str
    chat_id: str
    session_key: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SessionTurnStarted:
    """A turn has started processing for one session."""

    context: RuntimeEventContext


@dataclass(frozen=True, slots=True)
class TurnRunStatusChanged:
    """The runner changed visible execution state."""

    context: RuntimeEventContext
    status: str
    iteration: int | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class TurnOutputDelta:
    """A streamed text delta was produced for the current turn."""

    context: RuntimeEventContext
    delta: str
    iteration: int | None = None


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """A tool call is about to execute."""

    context: RuntimeEventContext
    iteration: int
    tool_name: str
    tool_call_id: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCallCompleted:
    """A tool call finished and produced a result preview."""

    context: RuntimeEventContext
    iteration: int
    tool_name: str
    tool_call_id: str | None = None
    result_preview: str = ""
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    """One turn finished processing."""

    context: RuntimeEventContext
    stop_reason: str
    final_content: str | None = None
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    latency_ms: int | None = None


RuntimeEvent = (
    SessionTurnStarted
    | TurnRunStatusChanged
    | TurnOutputDelta
    | ToolCallStarted
    | ToolCallCompleted
    | TurnCompleted
)
RuntimeEventType = (
    type[SessionTurnStarted]
    | type[TurnRunStatusChanged]
    | type[TurnOutputDelta]
    | type[ToolCallStarted]
    | type[ToolCallCompleted]
    | type[TurnCompleted]
)
RuntimeEventHandler = Callable[[RuntimeEvent], Awaitable[None] | None]
_HandlerEntry = tuple[RuntimeEventType | None, RuntimeEventHandler]


class RuntimeEventBus:
    """Small in-process pub/sub bus for runtime state."""

    def __init__(self) -> None:
        self._handlers: list[_HandlerEntry] = []

    def subscribe(
        self,
        handler: RuntimeEventHandler,
        event_type: RuntimeEventType | None = None,
    ) -> Callable[[], None]:
        entry = (event_type, handler)
        self._handlers.append(entry)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._handlers.remove(entry)

        return _unsubscribe

    async def publish(self, event: RuntimeEvent) -> None:
        for event_type, handler in list(self._handlers):
            if event_type is not None and not isinstance(event, event_type):
                continue
            result = handler(event)
            if inspect.isawaitable(result):
                await result

    def publish_nowait(self, event: RuntimeEvent) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish(event))


def build_runtime_event_context(
    *,
    channel: str | None,
    chat_id: str | None,
    session_key: str | None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeEventContext:
    """Create a normalized runtime event context."""

    return RuntimeEventContext(
        channel=str(channel or "cli"),
        chat_id=str(chat_id or session_key or "default"),
        session_key=str(session_key or "default"),
        metadata=deepcopy(metadata or {}),
    )


def _preview_text(value: str, *, limit: int = 300) -> str:
    if len(value) <= limit:
        return value
    head = max(1, limit // 2)
    tail = max(1, limit - head)
    omitted = len(value) - limit
    return value[:head] + f"\n\n... ({omitted:,} chars omitted) ...\n\n" + value[-tail:]


class RuntimeEventHook(AgentHook):
    """Adapt runner lifecycle callbacks into runtime events."""

    def __init__(
        self,
        bus: RuntimeEventBus,
        *,
        channel: str | None,
        chat_id: str | None,
        session_key: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._bus = bus
        self._context = build_runtime_event_context(
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            metadata=metadata,
        )

    def wants_streaming(self) -> bool:
        return True

    async def before_run(self, context: AgentRunHookContext) -> None:
        await self._bus.publish(
            TurnRunStatusChanged(context=self._context, status="running")
        )

    async def before_iteration(self, context: AgentHookContext) -> None:
        await self._bus.publish(
            TurnRunStatusChanged(
                context=self._context,
                status="calling_model",
                iteration=context.iteration,
            )
        )

    async def on_text_delta(self, context: AgentHookContext, delta: str) -> None:
        await self._bus.publish(
            TurnOutputDelta(
                context=self._context,
                delta=delta,
                iteration=context.iteration,
            )
        )

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        await self._bus.publish(
            TurnRunStatusChanged(
                context=self._context,
                status="executing_tools",
                iteration=context.iteration,
            )
        )
        for tool_call in context.tool_calls:
            await self._bus.publish(
                ToolCallStarted(
                    context=self._context,
                    iteration=context.iteration,
                    tool_name=str(tool_call.name),
                    tool_call_id=str(tool_call.id or "") or None,
                    arguments=deepcopy(tool_call.arguments),
                )
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        if context.tool_calls:
            for tool_call, result in zip(context.tool_calls, context.tool_results, strict=False):
                text = result if isinstance(result, str) else str(result)
                await self._bus.publish(
                    ToolCallCompleted(
                        context=self._context,
                        iteration=context.iteration,
                        tool_name=str(tool_call.name),
                        tool_call_id=str(tool_call.id or "") or None,
                        result_preview=_preview_text(text),
                        is_error=text.startswith("Error"),
                    )
                )

    async def after_run(self, context: AgentRunHookContext) -> None:
        await self._bus.publish(
            TurnRunStatusChanged(
                context=self._context,
                status=str(context.stop_reason or "completed"),
            )
        )

    async def on_error(self, context: AgentRunHookContext) -> None:
        await self._bus.publish(
            TurnRunStatusChanged(
                context=self._context,
                status="error",
                detail=str(context.error or context.exception or "runtime error"),
            )
        )
