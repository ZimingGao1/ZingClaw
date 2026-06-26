"""Async message bus for decoupled channel-agent communication."""

from __future__ import annotations

import asyncio

from zingclaw.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """Minimal queue-based message bus.

    Channels publish inbound user messages for the agent runtime to consume.
    The agent runtime publishes outbound replies for channels to deliver.
    """

    def __init__(self) -> None:
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish one inbound message from a channel into the bus."""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message, waiting until one is available."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish one outbound message from the runtime back to channels."""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message, waiting until one is available."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Return the number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Return the number of pending outbound messages."""
        return self.outbound.qsize()
