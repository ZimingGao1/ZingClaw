from __future__ import annotations

import pytest

from zingclaw.bus import InboundMessage, MessageBus, OutboundMessage
from zingclaw.channels import BaseChannel, ChannelManager


class RecordingChannel(BaseChannel):
    def __init__(
        self,
        bus: MessageBus,
        *,
        name: str = "cli",
        kind: str = "local",
        supports_streaming: bool = False,
    ) -> None:
        super().__init__(bus)
        self.name = name
        self.kind = kind
        self.supports_streaming = supports_streaming
        self.started = 0
        self.stopped = 0
        self.delivered: list[OutboundMessage] = []

    async def start(self) -> None:
        self.started += 1
        self._mark_running()

    async def stop(self) -> None:
        self.stopped += 1
        self._mark_stopped()

    async def deliver_outbound(self, msg: OutboundMessage) -> None:
        self.delivered.append(msg)


@pytest.mark.asyncio
async def test_base_channel_publish_inbound_routes_message_to_shared_bus() -> None:
    bus = MessageBus()
    channel = RecordingChannel(bus, name="cli")
    inbound = InboundMessage(
        channel="cli",
        chat_id="chat-1",
        session_key="demo",
        sender_id="user-1",
        text="hello from channel",
        metadata={"source": "manual"},
    )

    await channel.publish_inbound(inbound)

    consumed = await bus.consume_inbound()

    assert consumed.channel == "cli"
    assert consumed.chat_id == "chat-1"
    assert consumed.session_key == "demo"
    assert consumed.sender_id == "user-1"
    assert consumed.text == "hello from channel"
    assert consumed.metadata == {"source": "manual"}


@pytest.mark.asyncio
async def test_base_channel_rejects_inbound_message_for_different_channel_name() -> None:
    bus = MessageBus()
    channel = RecordingChannel(bus, name="cli")
    inbound = InboundMessage(
        channel="telegram",
        chat_id="chat-1",
        session_key="demo",
        text="wrong route",
    )

    with pytest.raises(ValueError, match="inbound message channel mismatch"):
        await channel.publish_inbound(inbound)


def test_channel_manager_rejects_duplicate_channel_names() -> None:
    bus = MessageBus()
    manager = ChannelManager(bus)
    manager.register(RecordingChannel(bus, name="cli"))

    with pytest.raises(ValueError, match="already registered"):
        manager.register(RecordingChannel(bus, name="cli"))


def test_channel_manager_rejects_channel_bound_to_different_bus() -> None:
    shared_bus = MessageBus()
    foreign_bus = MessageBus()
    manager = ChannelManager(shared_bus)

    with pytest.raises(ValueError, match="different MessageBus"):
        manager.register(RecordingChannel(foreign_bus, name="cli"))


@pytest.mark.asyncio
async def test_channel_manager_dispatches_outbound_to_matching_registered_channel() -> None:
    bus = MessageBus()
    channel = RecordingChannel(bus, name="cli")
    manager = ChannelManager(bus, [channel])
    outbound = OutboundMessage(
        channel="cli",
        chat_id="chat-1",
        session_key="demo",
        text="done",
        reply_to="msg-1",
        metadata={"stop_reason": "completed"},
    )

    await bus.publish_outbound(outbound)

    delivered = await manager.dispatch_once()

    assert delivered is True
    assert len(channel.delivered) == 1
    assert channel.delivered[0].text == "done"
    assert channel.delivered[0].reply_to == "msg-1"
    assert channel.delivered[0].metadata == {"stop_reason": "completed"}


@pytest.mark.asyncio
async def test_channel_manager_start_and_stop_manage_channel_lifecycle() -> None:
    bus = MessageBus()
    channel = RecordingChannel(bus, name="cli")
    manager = ChannelManager(bus, [channel])

    await manager.start_all()

    assert manager.is_dispatching is True
    assert channel.started == 1
    assert channel.is_running is True
    assert manager.enabled_channels == ["cli"]

    await manager.stop_all()

    assert manager.is_dispatching is False
    assert channel.stopped == 1
    assert channel.is_running is False
