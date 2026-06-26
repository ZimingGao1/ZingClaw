"""Base channel abstractions for chat and local runtime surfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod

from zingclaw.bus import InboundMessage, MessageBus, OutboundMessage


class BaseChannel(ABC):
    """Abstract channel interface for MessageBus-connected runtimes."""

    name: str = "base"
    kind: str = "generic"
    supports_streaming: bool = False

    def __init__(self, bus: MessageBus) -> None:
        self.bus = bus
        self._running = False

    @property
    def is_running(self) -> bool:
        """Return whether the channel is currently running."""
        return self._running

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Forward one inbound message into the shared message bus."""
        if msg.channel != self.name:
            raise ValueError(
                f"inbound message channel mismatch: expected '{self.name}', got '{msg.channel}'"
            )
        await self.bus.publish_inbound(msg)

    def _mark_running(self) -> None:
        self._running = True

    def _mark_stopped(self) -> None:
        self._running = False

    @abstractmethod
    async def start(self) -> None:
        """Start channel resources and begin accepting input."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop channel resources and end input/output activity."""

    @abstractmethod
    async def deliver_outbound(self, msg: OutboundMessage) -> None:
        """Deliver one outbound message addressed to this channel."""


Channel = BaseChannel
