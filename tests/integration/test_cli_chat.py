from __future__ import annotations

from dataclasses import dataclass

from typer.testing import CliRunner

import zingclaw.cli.main as cli_main
from zingclaw.cli.main import app
from zingclaw.config.settings import reset_settings_cache


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
        self.closed = False

    async def process_message(
        self,
        message: str,
        *,
        session_key: str,
        channel: str,
        chat_id: str,
        on_text_delta=None,
    ):
        assert channel == "cli"
        assert chat_id == session_key
        self.calls.append((session_key, message))
        if on_text_delta is not None:
            await on_text_delta(f"assistant:{message}")
        return FakeResult(final_content=f"assistant:{message}")

    def close(self) -> None:
        self.closed = True


def test_chat_one_shot_uses_agent_loop(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ZINGCLAW_HOME", str(tmp_path))
    reset_settings_cache()
    fake_loop = FakeLoop()
    monkeypatch.setattr(cli_main.AgentLoop, "from_settings", classmethod(lambda cls, settings: fake_loop))

    result = CliRunner().invoke(app, ["chat", "--session", "demo", "--message", "hello"])

    assert result.exit_code == 0
    assert result.stdout.count("assistant:hello") == 1
    assert fake_loop.calls == [("demo", "hello")]
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
    assert fake_loop.closed is True

    reset_settings_cache()
