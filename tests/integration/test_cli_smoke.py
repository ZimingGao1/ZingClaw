from typer.testing import CliRunner

from zingclaw.cli.main import app
from zingclaw.config.settings import reset_settings_cache


def test_status_command_runs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ZINGCLAW_HOME", str(tmp_path))
    reset_settings_cache()

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0
    assert "ZingClaw Status" in result.stdout
    assert "workspace" in result.stdout

    reset_settings_cache()
