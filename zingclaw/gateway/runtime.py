"""Runtime bridge that exposes bus and lifecycle events to API/WebUI consumers."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from zingclaw.agent import AgentLoop
from zingclaw.bus import InboundMessage, MessageBus
from zingclaw.gateway.events import GatewayEvent, encode_outbound_message, encode_runtime_event


@dataclass(slots=True)
class GatewaySubscription:
    """One filtered consumer view over gateway events."""

    queue: asyncio.Queue[GatewayEvent]
    session_key: str | None = None
    channel: str | None = None
    chat_id: str | None = None
    _closed: bool = False

    async def next(self) -> GatewayEvent:
        return await self.queue.get()

    def close(self) -> None:
        self._closed = True


@dataclass(slots=True)
class GatewayRuntime:
    """Own an ``AgentLoop`` task and fan out its events to UI/API subscribers."""

    loop: AgentLoop
    bus: MessageBus = field(default_factory=MessageBus)
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _pump_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _unsubscribe_runtime: Any = field(default=None, init=False, repr=False)
    _subscriptions: list[GatewaySubscription] = field(default_factory=list, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.loop.bind_bus(self.bus)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self.loop.run())
        self._pump_task = asyncio.create_task(self._pump_outbound())
        self._unsubscribe_runtime = self.loop.runtime_events.subscribe(self._on_runtime_event)

    async def stop(self) -> None:
        if self._started:
            self._started = False
            if callable(self._unsubscribe_runtime):
                self._unsubscribe_runtime()
                self._unsubscribe_runtime = None
            self.loop.stop()
            for task in (self._pump_task, self._task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (self._pump_task, self._task) if task is not None),
                return_exceptions=True,
            )
            self._pump_task = None
            self._task = None
        for subscription in list(self._subscriptions):
            subscription.close()
        self._subscriptions.clear()
        await self.loop.aclose()

    def subscribe(
        self,
        *,
        session_key: str | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> GatewaySubscription:
        subscription = GatewaySubscription(
            queue=asyncio.Queue(),
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
        )
        self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: GatewaySubscription) -> None:
        subscription.close()
        with contextlib.suppress(ValueError):
            self._subscriptions.remove(subscription)

    async def submit_inbound(self, message: InboundMessage) -> str:
        await self.start()
        if not message.message_id:
            message.message_id = uuid.uuid4().hex
        await self.bus.publish_inbound(message)
        return str(message.message_id)

    async def submit_user_message(
        self,
        *,
        text: str,
        session_key: str,
        channel: str = "api",
        chat_id: str | None = None,
        sender_id: str = "user",
        metadata: dict[str, Any] | None = None,
        wants_stream: bool = False,
        message_id: str | None = None,
    ) -> str:
        payload = dict(metadata or {})
        if wants_stream:
            payload["_wants_stream"] = True
        return await self.submit_inbound(
            InboundMessage(
                channel=channel,
                chat_id=chat_id or session_key,
                session_key=session_key,
                sender_id=sender_id,
                message_id=message_id or uuid.uuid4().hex,
                text=text,
                metadata=payload,
            )
        )

    async def _pump_outbound(self) -> None:
        while True:
            try:
                outbound = await self.bus.consume_outbound()
            except asyncio.CancelledError:
                break
            self._publish(encode_outbound_message(outbound))

    def _on_runtime_event(self, event: Any) -> None:
        self._publish(encode_runtime_event(event))

    def _publish(self, event: GatewayEvent) -> None:
        for subscription in list(self._subscriptions):
            if subscription._closed:
                self.unsubscribe(subscription)
                continue
            if subscription.session_key is not None and subscription.session_key != event.session_key:
                continue
            if subscription.channel is not None and subscription.channel != event.channel:
                continue
            if subscription.chat_id is not None and subscription.chat_id != event.chat_id:
                continue
            subscription.queue.put_nowait(event)
