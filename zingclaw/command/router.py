"""Minimal command routing for slash commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from zingclaw.bus.events import OutboundMessage

Handler = Callable[["CommandContext"], Awaitable[OutboundMessage | None]]


@dataclass(slots=True)
class CommandContext:
    """Everything one command handler needs to produce a response."""

    raw: str
    session_key: str
    channel: str
    chat_id: str
    loop: Any
    session: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    args: str = ""

    def reply(
        self,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        final: bool = True,
    ) -> OutboundMessage:
        payload = dict(self.metadata)
        if metadata:
            payload.update(metadata)
        return OutboundMessage(
            channel=self.channel,
            chat_id=self.chat_id,
            session_key=self.session_key,
            text=text,
            final=final,
            metadata=payload,
        )


class CommandRouter:
    """Pure dict-based command router for exact and prefix slash commands."""

    def __init__(self) -> None:
        self._exact: dict[str, Handler] = {}
        self._prefix: list[tuple[str, Handler]] = []

    def exact(self, command: str, handler: Handler) -> None:
        self._exact[command.lower()] = handler

    def prefix(self, prefix: str, handler: Handler) -> None:
        self._prefix.append((prefix.lower(), handler))
        self._prefix.sort(key=lambda item: len(item[0]), reverse=True)

    def is_command(self, text: str) -> bool:
        text = text.strip().lower()
        if text in self._exact:
            return True
        return any(text.startswith(prefix) for prefix, _handler in self._prefix)

    async def dispatch(self, ctx: CommandContext) -> OutboundMessage | None:
        raw = ctx.raw.strip()
        normalized = raw.lower()

        handler = self._exact.get(normalized)
        if handler is not None:
            return await handler(ctx)

        for prefix, prefixed_handler in self._prefix:
            if normalized.startswith(prefix):
                ctx.args = raw[len(prefix) :].strip()
                return await prefixed_handler(ctx)

        return None
