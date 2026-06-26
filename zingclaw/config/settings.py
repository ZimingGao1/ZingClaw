"""Minimal runtime settings for the first ZingClaw implementation slice."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_home_dir() -> Path:
    return Path.cwd()


class Settings(BaseSettings):
    """Application settings loaded from env vars or a local `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        protected_namespaces=(),
    )

    home_dir: Path = Field(
        default_factory=_default_home_dir,
        validation_alias=AliasChoices("ZINGCLAW_HOME", "ZINGCLAW_HOME_DIR"),
    )
    workspace_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("WORKSPACE_DIR", "ZINGCLAW_WORKSPACE_DIR"),
    )
    sessions_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("SESSIONS_DIR", "ZINGCLAW_SESSIONS_DIR"),
    )
    memory_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("MEMORY_DIR", "ZINGCLAW_MEMORY_DIR"),
    )
    logs_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("LOGS_DIR", "ZINGCLAW_LOGS_DIR"),
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "ZINGCLAW_OPENAI_API_KEY"),
    )
    openai_api_base: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_BASE", "ZINGCLAW_OPENAI_API_BASE"),
    )
    model_name: str = Field(
        default="openai/gpt-4.1-mini",
        validation_alias=AliasChoices("MODEL_NAME", "ZINGCLAW_MODEL_NAME"),
    )
    max_iterations: int = Field(
        default=20,
        validation_alias=AliasChoices("MAX_ITERATIONS", "ZINGCLAW_MAX_ITERATIONS"),
    )
    max_tool_result_chars: int = Field(
        default=16_000,
        validation_alias=AliasChoices(
            "MAX_TOOL_RESULT_CHARS",
            "ZINGCLAW_MAX_TOOL_RESULT_CHARS",
        ),
    )

    @model_validator(mode="after")
    def _populate_runtime_dirs(self) -> "Settings":
        home = self.home_dir.expanduser().resolve()
        self.home_dir = home
        self.workspace_dir = (self.workspace_dir or home / "workspace").expanduser().resolve()
        self.sessions_dir = (self.sessions_dir or home / "sessions").expanduser().resolve()
        self.memory_dir = (self.memory_dir or home / "memory").expanduser().resolve()
        self.logs_dir = (self.logs_dir or home / "logs").expanduser().resolve()
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def ensure_runtime_dirs(settings: Settings) -> None:
    for path in (
        settings.workspace_dir,
        settings.sessions_dir,
        settings.memory_dir,
        settings.logs_dir,
    ):
        assert path is not None
        path.mkdir(parents=True, exist_ok=True)
