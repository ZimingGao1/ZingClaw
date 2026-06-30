from __future__ import annotations

from zingclaw.bus import (
    OutboundMessage,
    ToolCallStarted,
    build_runtime_event_context,
)
from zingclaw.gateway import encode_outbound_message, encode_runtime_event


def test_encode_runtime_event_preserves_visible_fields() -> None:
    event = ToolCallStarted(
        context=build_runtime_event_context(
            channel="websocket",
            chat_id="chat-1",
            session_key="sess-1",
            metadata={"_request_id": "req-1"},
        ),
        iteration=2,
        tool_name="read_file",
        tool_call_id="call_1",
        arguments={"path": "notes.txt"},
    )

    encoded = encode_runtime_event(event).to_dict()

    assert encoded == {
        "kind": "runtime",
        "event_type": "tool_call_started",
        "channel": "websocket",
        "chat_id": "chat-1",
        "session_key": "sess-1",
        "payload": {
            "context": {
                "channel": "websocket",
                "chat_id": "chat-1",
                "session_key": "sess-1",
                "metadata": {"_request_id": "req-1"},
            },
            "iteration": 2,
            "tool_name": "read_file",
            "tool_call_id": "call_1",
            "arguments": {"path": "notes.txt"},
        },
    }


def test_encode_outbound_message_keeps_reply_and_stream_flags() -> None:
    message = OutboundMessage(
        channel="api",
        chat_id="api:default",
        session_key="api:default",
        text="hello",
        reply_to="msg-1",
        final=False,
        metadata={"_stream_delta": True},
    )

    encoded = encode_outbound_message(message).to_dict()

    assert encoded["kind"] == "outbound"
    assert encoded["event_type"] == "outbound_message"
    assert encoded["payload"]["reply_to"] == "msg-1"
    assert encoded["payload"]["metadata"]["_stream_delta"] is True
    assert encoded["payload"]["text"] == "hello"
