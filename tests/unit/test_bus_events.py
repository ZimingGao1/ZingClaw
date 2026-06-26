from __future__ import annotations

from datetime import datetime

from zingclaw.bus import InboundMessage, OutboundMessage


def test_inbound_message_exposes_required_fields_and_defaults() -> None:
    msg = InboundMessage(
        channel="cli",
        chat_id="local-chat",
        session_key="demo",
        message_id="msg-1",
        sender_id="user-1",
        text="hello",
    )

    assert msg.channel == "cli"
    assert msg.chat_id == "local-chat"
    assert msg.session_key == "demo"
    assert msg.message_id == "msg-1"
    assert msg.sender_id == "user-1"
    assert msg.text == "hello"
    assert msg.current_role == "user"
    assert msg.attachments == []
    assert msg.metadata == {}
    assert msg.extra_runtime_lines == []
    assert isinstance(msg.created_at, datetime)


def test_inbound_message_to_loop_kwargs_carries_needed_fields() -> None:
    msg = InboundMessage(
        channel="cli",
        chat_id="local-chat",
        session_key="demo",
        message_id="msg-1",
        sender_id="user-1",
        thread_id="thread-9",
        text="list files",
        metadata={"source": "interactive"},
        attachments=[{"type": "text", "name": "note.txt"}],
        extra_runtime_lines=["Mode: interactive"],
    )

    loop_kwargs = msg.to_loop_kwargs()

    assert loop_kwargs["message"] == "list files"
    assert loop_kwargs["session_key"] == "demo"
    assert loop_kwargs["channel"] == "cli"
    assert loop_kwargs["chat_id"] == "local-chat"
    assert loop_kwargs["current_role"] == "user"
    assert loop_kwargs["extra_runtime_lines"] == ["Mode: interactive"]
    assert loop_kwargs["metadata"] == {
        "source": "interactive",
        "message_id": "msg-1",
        "sender_id": "user-1",
        "thread_id": "thread-9",
        "attachments": [{"type": "text", "name": "note.txt"}],
    }


def test_outbound_message_serializes_reply_and_final_state() -> None:
    msg = OutboundMessage(
        channel="cli",
        chat_id="local-chat",
        session_key="demo",
        text="working...",
        reply_to="msg-1",
        message_id="out-1",
        final=False,
        metadata={"stop_reason": "tool_calls"},
    )

    payload = msg.to_dict()

    assert payload["kind"] == "outbound"
    assert payload["reply_to"] == "msg-1"
    assert payload["message_id"] == "out-1"
    assert payload["final"] is False
    assert payload["metadata"] == {"stop_reason": "tool_calls"}
    assert payload["text"] == "working..."
