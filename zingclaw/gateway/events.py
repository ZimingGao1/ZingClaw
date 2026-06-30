"""Gateway-facing event envelopes built from bus/runtime primitives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from zingclaw.bus.events import OutboundMessage
from zingclaw.bus.runtime_events import RuntimeEvent


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


@dataclass(frozen=True, slots=True)
class GatewayEvent:
    """One normalized event pushed toward API/WebUI consumers."""

    kind: str
    event_type: str
    channel: str
    chat_id: str
    session_key: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "event_type": self.event_type,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "session_key": self.session_key,
            "payload": dict(self.payload),
        }


def encode_runtime_event(event: RuntimeEvent) -> GatewayEvent:
    """Convert one runtime event dataclass into a stable wire envelope."""

    context = event.context
    payload: dict[str, Any] = {
        "context": {
            "channel": context.channel,
            "chat_id": context.chat_id,
            "session_key": context.session_key,
            "metadata": dict(context.metadata),
        }
    }
    for field_name in getattr(type(event), "__dataclass_fields__", {}):
        if field_name == "context":
            continue
        payload[field_name] = getattr(event, field_name)
    event_type = _camel_to_snake(type(event).__name__)
    return GatewayEvent(
        kind="runtime",
        event_type=event_type,
        channel=context.channel,
        chat_id=context.chat_id,
        session_key=context.session_key,
        payload=payload,
    )


def encode_outbound_message(message: OutboundMessage) -> GatewayEvent:
    """Convert one outbound bus message into a gateway envelope."""

    return GatewayEvent(
        kind="outbound",
        event_type="outbound_message",
        channel=message.channel,
        chat_id=message.chat_id,
        session_key=message.session_key,
        payload=message.to_dict(),
    )
