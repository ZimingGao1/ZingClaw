from pathlib import Path

from zingclaw.config.settings import Settings, ensure_runtime_dirs


def test_settings_fill_runtime_dirs_from_home(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)

    assert settings.provider_name == "openai_compat"
    assert settings.provider.model == "openai/gpt-4.1-mini"
    assert settings.workspace_dir == tmp_path / "workspace"
    assert settings.sessions_dir == tmp_path / "sessions"
    assert settings.memory_dir == tmp_path / "memory"
    assert settings.logs_dir == tmp_path / "logs"
    assert settings.skills_dir == tmp_path / "skills"
    assert settings.paths.skills_dir == tmp_path / "skills"
    assert settings.agent.max_iterations == 20
    assert settings.agent.max_concurrent_subagents == 1
    assert settings.agent.max_tool_result_chars == 16_000
    assert settings.agent.enabled_skills == []
    assert settings.agent.disabled_skills == []
    assert settings.security.restrict_to_workspace is True
    assert settings.security.exec_sandbox == ""


def test_ensure_runtime_dirs_creates_directories(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)

    ensure_runtime_dirs(settings)

    assert settings.workspace_dir is not None and settings.workspace_dir.is_dir()
    assert settings.sessions_dir is not None and settings.sessions_dir.is_dir()
    assert settings.memory_dir is not None and settings.memory_dir.is_dir()
    assert settings.logs_dir is not None and settings.logs_dir.is_dir()
    assert settings.skills_dir is not None and settings.skills_dir.is_dir()


def test_settings_accepts_provider_name_from_env_alias(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PROVIDER_NAME", "openai-compat")

    settings = Settings(_env_file=None, home_dir=tmp_path)

    assert settings.provider_name == "openai_compat"
    assert settings.provider.name == "openai_compat"
