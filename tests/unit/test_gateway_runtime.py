from __future__ import annotations

import asyncio

import pytest

from zingclaw.bus import OutboundMessage, RuntimeEventBus, TurnRunStatusChanged, build_runtime_event_context
from zingclaw.gateway import GatewayRuntime


class FakeLoop:
    def __init__(self) -> None:
        self.bus = None
        self._running = False
        self.runtime_events = RuntimeEventBus()
        self.closed = False

    def bind_bus(self, bus) -> None:
        self.bus = bus

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                inbound = await asyncio.wait_for(self.bus.consume_inbound(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            context = build_runtime_event_context(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                session_key=inbound.session_key,
                metadata=dict(inbound.metadata),
            )
            await self.runtime_events.publish(
                TurnRunStatusChanged(context=context, status="calling_model", iteration=1)
            )
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    session_key=inbound.session_key,
                    text=f"assistant:{inbound.text}",
                    reply_to=inbound.message_id,
                    final=True,
                    metadata={"stop_reason": "completed"},
                )
            )

    def stop(self) -> None:
        self._running = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_gateway_runtime_fans_out_runtime_and_outbound_events() -> None:
    runtime = GatewayRuntime(FakeLoop())
    await runtime.start()
    subscription = runtime.subscribe(session_key="demo", channel="api")

    try:
        message_id = await runtime.submit_user_message(
            text="hello",
            session_key="demo",
            channel="api",
            chat_id="demo",
            metadata={"_request_id": "req-1"},
        )

        first = await asyncio.wait_for(subscription.next(), timeout=1.0)
        second = await asyncio.wait_for(subscription.next(), timeout=1.0)
    finally:
        runtime.unsubscribe(subscription)
        await runtime.stop()

    rows = [first.to_dict(), second.to_dict()]

    assert rows[0]["kind"] == "runtime"
    assert rows[0]["event_type"] == "turn_run_status_changed"
    assert rows[0]["payload"]["status"] == "calling_model"
    assert rows[0]["payload"]["context"]["metadata"]["_request_id"] == "req-1"

    assert rows[1]["kind"] == "outbound"
    assert rows[1]["payload"]["reply_to"] == message_id
    assert rows[1]["payload"]["text"] == "assistant:hello"
    assert rows[1]["payload"]["metadata"]["stop_reason"] == "completed"
