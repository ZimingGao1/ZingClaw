from __future__ import annotations

import asyncio
from dataclasses import dataclass

import zingclaw.cli.main as cli_main
from typer.testing import CliRunner
from zingclaw.bus import (
    OutboundMessage,
    RuntimeEventBus,
    ToolCallCompleted,
    ToolCallStarted,
    TurnRunStatusChanged,
    build_runtime_event_context,
)
from zingclaw.cli.main import app
from zingclaw.config.settings import reset_settings_cache
from zingclaw.tools import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_ACK,
    RUNTIME_CONTROL_MCP_RELOAD,
    MCPServerStatus,
)


@dataclass
class FakeResult:
    final_content: str | None
    stop_reason: str = "completed"
    error: str | None = None


class FakeSession:
    def __init__(self) -> None:
        self.cleared = False
        self.saved = False

    def clear(self) -> None:
        self.cleared = True


class FakeSessions:
    def __init__(self) -> None:
        self.session = FakeSession()

    def get_or_create(self, key: str) -> FakeSession:
        assert key == "demo"
        return self.session

    def save(self, session: FakeSession) -> None:
        assert session is self.session
        self.session.saved = True


class FakeLoop:
    def __init__(self) -> None:
        self.settings = type(
            "FakeSettings",
            (),
            {
                "model_name": "fake-model",
                "workspace_dir": "D:/fake/workspace",
            },
        )()
        self.sessions = FakeSessions()
        self.calls: list[tuple[str, str]] = []
        self.bound_bus_count = 0
        self.closed = False
        self._running = False
        self.bus = None
        self.reload_calls = 0
        self.runtime_events = RuntimeEventBus()

    def drain_cron_notifications(self, session_key: str) -> list[str]:
        assert session_key == "demo"
        return []

    def drain_subagent_notifications(self, session_key: str) -> list[str]:
        assert session_key == "demo"
        return []

    def bind_bus(self, bus) -> None:
        self.bus = bus
        self.bound_bus_count += 1

    async def get_mcp_status(self, *, connect_missing: bool = True):
        assert connect_missing in {True, False}
        return [
            MCPServerStatus(
                name="demo",
                transport="stdio",
                connected=True,
                tool_prefix="mcp_demo_",
                target="python demo_server.py",
                capability_count=4,
            )
        ]

    async def reload_mcp(self):
        self.reload_calls += 1
        return {"ok": True, "message": "MCP config reloaded without restarting ZingClaw."}

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                inbound = await asyncio.wait_for(self.bus.consume_inbound(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            control = inbound.metadata.get(INBOUND_META_RUNTIME_CONTROL)
            if control == RUNTIME_CONTROL_MCP_RELOAD:
                self.reload_calls += 1
                ack = inbound.metadata.get(RUNTIME_CONTROL_ACK)
                if isinstance(ack, asyncio.Future) and not ack.done():
                    ack.set_result(
                        {"ok": True, "message": "MCP config reloaded without restarting ZingClaw."}
                    )
                continue
            self.calls.append((inbound.session_key, inbound.text))
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    session_key=inbound.session_key,
                    text=f"assistant:{inbound.text}",
                    final=True,
                    metadata={"stop_reason": "completed"},
                )
            )

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self.closed = True


class FakeRuntimeLoop(FakeLoop):
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
                metadata=dict(inbound.metadata or {}),
            )
            await self.runtime_events.publish(TurnRunStatusChanged(context=context, status="building_context"))
            await self.runtime_events.publish(TurnRunStatusChanged(context=context, status="calling_model"))
            await self.runtime_events.publish(
                ToolCallStarted(
                    context=context,
                    iteration=1,
                    tool_name="echo_tool",
                    tool_call_id="call_1",
                    arguments={"value": inbound.text},
                )
            )
            await self.runtime_events.publish(
                ToolCallCompleted(
                    context=context,
                    iteration=1,
                    tool_name="echo_tool",
                    tool_call_id="call_1",
                    result_preview='{"echo": "hello"}',
                    is_error=False,
                )
            )
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    session_key=inbound.session_key,
                    text=f"assistant:{inbound.text}",
                    final=True,
                    metadata={"stop_reason": "completed"},
                )
            )


def test_chat_one_shot_uses_agent_loop(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ZINGCLAW_HOME", str(tmp_path))
    reset_settings_cache()
    fake_loop = FakeLoop()
    monkeypatch.setattr(cli_main.AgentLoop, "from_settings", classmethod(lambda cls, settings: fake_loop))

    result = CliRunner().invoke(app, ["chat", "--session", "demo", "--message", "hello"])

    assert result.exit_code == 0
    assert result.stdout.count("assistant:hello") == 1
    assert fake_loop.calls == [("demo", "hello")]
    assert fake_loop.bound_bus_count == 1
    assert fake_loop.closed is True

    reset_settings_cache()


def test_chat_interactive_supports_commands_and_clear(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ZINGCLAW_HOME", str(tmp_path))
    reset_settings_cache()
    fake_loop = FakeLoop()
    monkeypatch.setattr(cli_main.AgentLoop, "from_settings", classmethod(lambda cls, settings: fake_loop))

    result = CliRunner().invoke(
        app,
        ["chat", "--session", "demo"],
        input="/model\n/session\n/clear\nhello\n/exit\n",
    )

    assert result.exit_code == 0
    assert "fake-model" in result.stdout
    assert "demo" in result.stdout
    assert "Cleared session `demo`." in result.stdout
    assert result.stdout.count("assistant:hello") == 1
    assert fake_loop.sessions.session.cleared is True
    assert fake_loop.sessions.session.saved is True
    assert fake_loop.calls == [("demo", "hello")]
    assert fake_loop.bound_bus_count == 1
    assert fake_loop.closed is True

    reset_settings_cache()


def test_chat_interactive_supports_mcp_commands(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ZINGCLAW_HOME", str(tmp_path))
    reset_settings_cache()
    fake_loop = FakeLoop()
    monkeypatch.setattr(cli_main.AgentLoop, "from_settings", classmethod(lambda cls, settings: fake_loop))

    result = CliRunner().invoke(
        app,
        ["chat", "--session", "demo"],
        input="/mcp\n/mcp reload\n/exit\n",
    )

    assert result.exit_code == 0
    assert "MCP Status" in result.stdout
    assert "demo" in result.stdout
    assert "mcp_demo_" in result.stdout
    assert "MCP config reloaded without restarting ZingClaw." in result.stdout
    assert fake_loop.reload_calls == 1
    assert fake_loop.closed is True

    reset_settings_cache()


def test_chat_one_shot_prints_runtime_event_progress(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ZINGCLAW_HOME", str(tmp_path))
    reset_settings_cache()
    fake_loop = FakeRuntimeLoop()
    monkeypatch.setattr(cli_main.AgentLoop, "from_settings", classmethod(lambda cls, settings: fake_loop))

    result = CliRunner().invoke(app, ["chat", "--session", "demo", "--message", "hello"])

    assert result.exit_code == 0
    assert "preparing context..." in result.stdout
    assert "thinking..." in result.stdout
    assert "tool: echo_tool" in result.stdout
    assert "tool done: echo_tool" in result.stdout
    assert "assistant:hello" in result.stdout
    assert fake_loop.closed is True

    reset_settings_cache()
