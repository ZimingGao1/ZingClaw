"""Channel manager for coordinating message-bus connected channels."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from zingclaw.bus import MessageBus, OutboundMessage
from zingclaw.channels.base import BaseChannel


class ChannelManager:
    """Register channels, manage lifecycle, and dispatch outbound messages."""

    def __init__(
        self,
        bus: MessageBus,
        channels: list[BaseChannel] | None = None,
    ) -> None:
        self.bus = bus
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task[None] | None = None

        for channel in channels or []:
            self.register(channel)

    def register(self, channel: BaseChannel) -> BaseChannel:
        """Register one channel instance by its declared name."""
        name = str(channel.name).strip()
        if not name:
            raise ValueError("channel.name must not be empty")
        if channel.bus is not self.bus:
            raise ValueError(
                f"channel '{name}' is bound to a different MessageBus instance"
            )
        if name in self.channels:
            raise ValueError(f"channel '{name}' is already registered")

        self.channels[name] = channel
        return channel

    def get_channel(self, name: str) -> BaseChannel | None:
        """Return one registered channel by name."""
        return self.channels.get(name)

    @property
    def enabled_channels(self) -> list[str]:
        """Return the names of all registered channels."""
        return list(self.channels.keys())

    @property
    def is_dispatching(self) -> bool:
        """Return whether the outbound dispatcher task is active."""
        return self._dispatch_task is not None and not self._dispatch_task.done()

    async def start_all(self) -> None:
        """Start the outbound dispatcher and all registered channels."""
        if self._dispatch_task is None or self._dispatch_task.done():
            self._dispatch_task = asyncio.create_task(self._dispatch_loop())

        if not self.channels:
            return

        await asyncio.gather(*(channel.start() for channel in self.channels.values()))

    async def stop_all(self) -> None:
        """Stop the outbound dispatcher and all registered channels."""
        dispatch_task = self._dispatch_task
        self._dispatch_task = None

        if dispatch_task is not None:
            dispatch_task.cancel()
            with suppress(asyncio.CancelledError):
                await dispatch_task

        if self.channels:
            await asyncio.gather(*(channel.stop() for channel in self.channels.values()))

    async def dispatch_once(self) -> bool:
        """Consume and route one outbound message. Return True when delivered."""
        msg = await self.bus.consume_outbound()
        return await self._deliver(msg)

    async def _dispatch_loop(self) -> None:
        while True:
            try:
                await self.dispatch_once()
            except asyncio.CancelledError:
                break

    async def _deliver(self, msg: OutboundMessage) -> bool:
        channel = self.get_channel(msg.channel)
        if channel is None:
            return False
        await channel.deliver_outbound(msg)
        return True
