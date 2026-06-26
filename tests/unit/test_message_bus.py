from __future__ import annotations

import pytest

from zingclaw.bus import InboundMessage, MessageBus, OutboundMessage


@pytest.mark.asyncio
async def test_message_bus_publish_and_consume_inbound_message() -> None:
    bus = MessageBus()
    inbound = InboundMessage(
        channel="cli",
        chat_id="chat-1",
        session_key="demo",
        sender_id="user-1",
        text="hello bus",
        metadata={"source": "interactive"},
    )

    await bus.publish_inbound(inbound)

    assert bus.inbound_size == 1

    consumed = await bus.consume_inbound()

    assert consumed.channel == "cli"
    assert consumed.chat_id == "chat-1"
    assert consumed.session_key == "demo"
    assert consumed.sender_id == "user-1"
    assert consumed.text == "hello bus"
    assert consumed.metadata == {"source": "interactive"}
    assert bus.inbound_size == 0


@pytest.mark.asyncio
async def test_message_bus_publish_and_consume_outbound_message() -> None:
    bus = MessageBus()
    outbound = OutboundMessage(
        channel="cli",
        chat_id="chat-1",
        session_key="demo",
        text="done",
        reply_to="msg-1",
        final=True,
        metadata={"stop_reason": "completed"},
    )

    await bus.publish_outbound(outbound)

    assert bus.outbound_size == 1

    consumed = await bus.consume_outbound()

    assert consumed.channel == "cli"
    assert consumed.chat_id == "chat-1"
    assert consumed.session_key == "demo"
    assert consumed.text == "done"
    assert consumed.reply_to == "msg-1"
    assert consumed.final is True
    assert consumed.metadata == {"stop_reason": "completed"}
    assert bus.outbound_size == 0


@pytest.mark.asyncio
async def test_message_bus_preserves_fifo_order_per_queue() -> None:
    bus = MessageBus()
    first = InboundMessage(
        channel="cli",
        chat_id="chat-1",
        session_key="demo",
        text="first",
    )
    second = InboundMessage(
        channel="cli",
        chat_id="chat-1",
        session_key="demo",
        text="second",
    )

    await bus.publish_inbound(first)
    await bus.publish_inbound(second)

    consumed_first = await bus.consume_inbound()
    consumed_second = await bus.consume_inbound()

    assert consumed_first.text == "first"
    assert consumed_second.text == "second"
