from __future__ import annotations

from pathlib import Path

from zingclaw.tools.sandbox import available_backends, wrap_command


def test_available_backends_lists_bwrap() -> None:
    assert "bwrap" in available_backends()


def test_wrap_command_bwrap_includes_workspace_mount_and_command(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "src"
    nested.mkdir(parents=True)

    wrapped = wrap_command("bwrap", "printf hello", str(workspace), str(nested))

    assert "bwrap" in wrapped
    assert "--bind" in wrapped
    assert str(workspace.resolve()) in wrapped
    assert "printf hello" in wrapped
    assert str(nested.resolve()) in wrapped
