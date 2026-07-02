from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from zingclaw.command import CommandContext, CommandRouter, build_help_text, format_mcp_status_text
from zingclaw.command.builtin import cmd_clear, cmd_mcp_reconnect, cmd_mcp_reload, register_builtin_commands
from zingclaw.tools import MCPServerStatus


class _FakeSession:
    def __init__(self) -> None:
        self.cleared = False

    def clear(self) -> None:
        self.cleared = True


class _FakeSessions:
    def __init__(self) -> None:
        self.session = _FakeSession()
        self.saved = False

    def get_or_create(self, key: str) -> _FakeSession:
        assert key == "demo"
        return self.session

    def save(self, session: _FakeSession) -> None:
        assert session is self.session
        self.saved = True


class _FakeLoop:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(model_name="fake-model")
        self.sessions = _FakeSessions()
        self.reload_calls = 0

    async def get_mcp_status(self, *, connect_missing: bool = True):
        return [
            MCPServerStatus(
                name="demo",
                transport="stdio",
                connected=True,
                tool_prefix="mcp_demo_",
                target="python demo.py",
                tool_count=2,
                resource_count=1,
                prompt_count=1,
                capability_count=4,
                tool_names=["mcp_demo_echo", "mcp_demo_shout"],
                resource_names=["mcp_demo_resource_greeting"],
                prompt_names=["mcp_demo_prompt_review_plan"],
            )
        ]

    async def reload_mcp(self) -> dict[str, Any]:
        self.reload_calls += 1
        return {"ok": True, "message": "reloaded"}

    async def reconnect_mcp(self, server_name: str | None = None) -> dict[str, Any]:
        self.reload_calls += 1
        return {"ok": True, "message": f"reconnected:{server_name or 'all'}"}


@pytest.mark.asyncio
async def test_command_router_dispatches_exact_and_prefix_commands() -> None:
    router = CommandRouter()

    async def _exact(ctx: CommandContext):
        return ctx.reply("exact")

    async def _prefix(ctx: CommandContext):
        return ctx.reply(f"prefix:{ctx.args}")

    router.exact("/hello", _exact)
    router.prefix("/echo ", _prefix)

    exact = await router.dispatch(
        CommandContext(
            raw="/hello",
            session_key="demo",
            channel="cli",
            chat_id="demo",
            loop=None,
        )
    )
    prefixed = await router.dispatch(
        CommandContext(
            raw="/echo ping pong",
            session_key="demo",
            channel="cli",
            chat_id="demo",
            loop=None,
        )
    )

    assert exact is not None and exact.text == "exact"
    assert prefixed is not None and prefixed.text == "prefix:ping pong"
    assert router.is_command("/hello") is True
    assert router.is_command("/echo ping") is True
    assert router.is_command("hello") is False


@pytest.mark.asyncio
async def test_builtin_clear_command_mutates_session_and_returns_message() -> None:
    loop = _FakeLoop()
    session = loop.sessions.get_or_create("demo")
    ctx = CommandContext(
        raw="/clear",
        session_key="demo",
        channel="cli",
        chat_id="demo",
        loop=loop,
        session=session,
    )

    outbound = await cmd_clear(ctx)

    assert session.cleared is True
    assert loop.sessions.saved is True
    assert outbound.text == "Cleared session `demo`."


@pytest.mark.asyncio
async def test_builtin_mcp_reload_command_returns_reload_message_and_status() -> None:
    loop = _FakeLoop()
    ctx = CommandContext(
        raw="/mcp reload",
        session_key="demo",
        channel="cli",
        chat_id="demo",
        loop=loop,
    )

    outbound = await cmd_mcp_reload(ctx)

    assert loop.reload_calls == 1
    assert "reloaded" in outbound.text
    assert "MCP Status" in outbound.text
    assert "mcp_demo_" in outbound.text


@pytest.mark.asyncio
async def test_builtin_mcp_reconnect_command_returns_reconnect_message_and_status() -> None:
    loop = _FakeLoop()
    ctx = CommandContext(
        raw="/mcp reconnect demo",
        session_key="demo",
        channel="cli",
        chat_id="demo",
        loop=loop,
        args="demo",
    )

    outbound = await cmd_mcp_reconnect(ctx)

    assert "reconnected:demo" in outbound.text
    assert "MCP Status" in outbound.text


def test_help_text_and_status_text_are_human_readable() -> None:
    help_text = build_help_text(include_exit=True)
    status_text = format_mcp_status_text(
        [
            MCPServerStatus(
                name="demo",
                transport="stdio",
                connected=True,
                tool_prefix="mcp_demo_",
                target="python demo.py",
                tool_count=2,
                resource_count=1,
                prompt_count=1,
                capability_count=4,
                tool_names=["mcp_demo_echo"],
            )
        ]
    )

    assert "`/help`" in help_text
    assert "`/exit`" in help_text
    assert "MCP Status" in status_text
    assert "demo: stdio, connected" in status_text
    assert "tools: mcp_demo_echo" in status_text


@pytest.mark.asyncio
async def test_register_builtin_commands_wires_known_chat_commands() -> None:
    router = CommandRouter()
    register_builtin_commands(router)
    loop = _FakeLoop()

    outbound = await router.dispatch(
        CommandContext(
            raw="/session",
            session_key="demo",
            channel="cli",
            chat_id="demo",
            loop=loop,
        )
    )

    assert outbound is not None
    assert outbound.text == "demo"
