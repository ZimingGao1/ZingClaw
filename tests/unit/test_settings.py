from pathlib import Path

from zingclaw.config.settings import Settings, ensure_runtime_dirs


def test_settings_fill_runtime_dirs_from_home(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)

    assert settings.workspace_dir == tmp_path / "workspace"
    assert settings.sessions_dir == tmp_path / "sessions"
    assert settings.memory_dir == tmp_path / "memory"
    assert settings.logs_dir == tmp_path / "logs"


def test_ensure_runtime_dirs_creates_directories(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)

    ensure_runtime_dirs(settings)

    assert settings.workspace_dir is not None and settings.workspace_dir.is_dir()
    assert settings.sessions_dir is not None and settings.sessions_dir.is_dir()
    assert settings.memory_dir is not None and settings.memory_dir.is_dir()
    assert settings.logs_dir is not None and settings.logs_dir.is_dir()
