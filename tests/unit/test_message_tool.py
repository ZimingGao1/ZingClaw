from __future__ import annotations

from pathlib import Path

import pytest

from zingclaw.bus import MessageBus
from zingclaw.config.settings import Settings
from zingclaw.tools import RequestContext, ToolContext
from zingclaw.tools.message import MessageTool


@pytest.mark.asyncio
async def test_message_tool_sends_to_current_context_and_attaches_media(tmp_path: Path) -> None:
    bus = MessageBus()
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("hello", encoding="utf-8")
    tool = MessageTool(send_callback=bus.publish_outbound, workspace=tmp_path)
    token = tool.set_context(
        RequestContext(
            session_key="demo",
            channel="cli",
            chat_id="chat-1",
            metadata={"message_id": "msg-1"},
        )
    )

    try:
        result = await tool.execute("please review", media=["artifact.txt"])
    finally:
        tool.reset_context(token)

    outbound = await bus.consume_outbound()

    assert result == "Message sent to cli:chat-1 with 1 attachment(s)"
    assert outbound.channel == "cli"
    assert outbound.chat_id == "chat-1"
    assert outbound.session_key == "demo"
    assert outbound.text == "please review"
    assert outbound.reply_to == "msg-1"
    assert outbound.attachments == [{"type": "file", "path": str(artifact.resolve())}]
    assert outbound.metadata == {"_tool_message": True, "source": "message_tool"}


@pytest.mark.asyncio
async def test_message_tool_cross_target_does_not_inherit_reply_to(tmp_path: Path) -> None:
    bus = MessageBus()
    tool = MessageTool(send_callback=bus.publish_outbound, workspace=tmp_path)
    token = tool.set_context(
        RequestContext(
            session_key="demo",
            channel="cli",
            chat_id="chat-1",
            metadata={"message_id": "msg-1"},
        )
    )

    try:
        result = await tool.execute(
            "send elsewhere",
            channel="telegram",
            chat_id="user-9",
            media=["https://example.com/image.png"],
        )
    finally:
        tool.reset_context(token)

    outbound = await bus.consume_outbound()

    assert result == "Message sent to telegram:user-9 with 1 attachment(s)"
    assert outbound.channel == "telegram"
    assert outbound.chat_id == "user-9"
    assert outbound.session_key == "demo"
    assert outbound.reply_to is None
    assert outbound.attachments == [{"type": "url", "url": "https://example.com/image.png"}]


@pytest.mark.asyncio
async def test_message_tool_rejects_outside_workspace_media(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    tool = MessageTool(
        send_callback=lambda message: None,
        workspace=tmp_path,
        restrict_to_workspace=True,
    )
    token = tool.set_context(
        RequestContext(
            session_key="demo",
            channel="cli",
            chat_id="chat-1",
        )
    )

    try:
        result = await tool.execute("blocked", media=[str(outside)])
    finally:
        tool.reset_context(token)

    assert result.startswith("Error: media path is not allowed:")


@pytest.mark.asyncio
async def test_message_tool_create_uses_tool_context_bus(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    bus = MessageBus()
    tool = MessageTool.create(ToolContext.from_settings(settings, bus=bus))
    token = tool.set_context(
        RequestContext(
            session_key="demo",
            channel="cli",
            chat_id="chat-1",
        )
    )

    try:
        result = await tool.execute("hello from create")
    finally:
        tool.reset_context(token)

    outbound = await bus.consume_outbound()

    assert result == "Message sent to cli:chat-1"
    assert outbound.text == "hello from create"


@pytest.mark.asyncio
async def test_message_tool_reports_missing_delivery_configuration(tmp_path: Path) -> None:
    tool = MessageTool(workspace=tmp_path)
    token = tool.set_context(
        RequestContext(
            session_key="demo",
            channel="cli",
            chat_id="chat-1",
        )
    )

    try:
        result = await tool.execute("hello")
    finally:
        tool.reset_context(token)

    assert result == "Error: Message sending not configured"
