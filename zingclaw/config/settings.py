"""Runtime settings plus structured config loading for ZingClaw."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from pydantic_settings.sources import InitSettingsSource

from zingclaw.config.schema import (
    AgentConfig,
    ApiConfig,
    ChannelConfig,
    LoggingConfig,
    MCPServerConfig,
    MemoryConfig,
    PathsConfig,
    ProviderConfig,
    SecurityConfig,
    WebUIConfig,
    ZingClawConfig,
)
from zingclaw.config.utils import default_home_dir, resolve_config_path, resolve_env_refs, resolve_runtime_dir


class _JsonConfigSettingsSource(InitSettingsSource):
    """Load config values from config.json and resolve ${ENV_VAR} references."""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        *,
        init_settings: PydanticBaseSettingsSource,
        env_file: str | Path | tuple[str | Path, ...] | None,
    ) -> None:
        init_kwargs = getattr(init_settings, "init_kwargs", {})
        config_path = init_kwargs.get("config_path") or init_kwargs.get("ZINGCLAW_CONFIG_PATH")
        home_dir = init_kwargs.get("home_dir") or init_kwargs.get("ZINGCLAW_HOME")
        resolved_path = resolve_config_path(
            config_path=config_path,
            home_dir=home_dir,
            env_file=env_file,
        )
        self.config_path = resolved_path
        data: dict[str, Any] = {}
        if resolved_path.exists():
            with resolved_path.open(encoding="utf-8") as handle:
                raw_data = json.load(handle)
            config_doc = ZingClawConfig.model_validate(
                resolve_env_refs(raw_data, env_file=env_file)
            )
            data = config_doc.model_dump(mode="python")
        super().__init__(settings_cls, data)


class Settings(BaseSettings):
    """Runtime settings that preserve flat access while exposing nested config."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        protected_namespaces=(),
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
    )

    config_path: Path | None = Field(
        default=None,
        exclude=True,
        validation_alias=AliasChoices("ZINGCLAW_CONFIG_PATH", "ZINGCLAW_CONFIG"),
    )
    name: str = Field(
        default="zingclaw",
        validation_alias=AliasChoices("ZINGCLAW_NAME", "BOT_NAME"),
    )
    identity: str = Field(
        default="",
        validation_alias=AliasChoices("ZINGCLAW_IDENTITY", "IDENTITY"),
    )
    home_dir: Path = Field(
        default_factory=default_home_dir,
        validation_alias=AliasChoices("ZINGCLAW_HOME", "ZINGCLAW_HOME_DIR"),
    )
    paths: PathsConfig = Field(default_factory=PathsConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    channels: list[ChannelConfig] = Field(default_factory=list)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    webui: WebUIConfig = Field(default_factory=WebUIConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
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
    skills_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("SKILLS_DIR", "ZINGCLAW_SKILLS_DIR"),
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "ZINGCLAW_OPENAI_API_KEY"),
    )
    openai_api_base: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_BASE", "ZINGCLAW_OPENAI_API_BASE"),
    )
    provider_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PROVIDER_NAME",
            "PROVIDER",
            "ZINGCLAW_PROVIDER_NAME",
            "ZINGCLAW_PROVIDER",
        ),
    )
    model_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MODEL_NAME", "ZINGCLAW_MODEL_NAME"),
    )
    max_iterations: int | None = Field(
        default=None,
        validation_alias=AliasChoices("MAX_ITERATIONS", "ZINGCLAW_MAX_ITERATIONS"),
    )
    max_concurrent_subagents: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MAX_CONCURRENT_SUBAGENTS",
            "ZINGCLAW_MAX_CONCURRENT_SUBAGENTS",
        ),
    )
    max_tool_result_chars: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MAX_TOOL_RESULT_CHARS",
            "ZINGCLAW_MAX_TOOL_RESULT_CHARS",
        ),
    )

    @model_validator(mode="after")
    def _synchronize_flat_and_nested_config(self) -> "Settings":
        home = self.home_dir.expanduser().resolve()
        self.home_dir = home

        self.paths.workspace_dir = resolve_runtime_dir(
            self.workspace_dir if self.workspace_dir is not None else self.paths.workspace_dir,
            home,
            "workspace",
        )
        self.paths.sessions_dir = resolve_runtime_dir(
            self.sessions_dir if self.sessions_dir is not None else self.paths.sessions_dir,
            home,
            "sessions",
        )
        self.paths.memory_dir = resolve_runtime_dir(
            self.memory_dir if self.memory_dir is not None else self.paths.memory_dir,
            home,
            "memory",
        )
        self.paths.logs_dir = resolve_runtime_dir(
            self.logs_dir if self.logs_dir is not None else self.paths.logs_dir,
            home,
            "logs",
        )
        self.paths.skills_dir = resolve_runtime_dir(
            self.skills_dir if self.skills_dir is not None else self.paths.skills_dir,
            home,
            "skills",
        )
        self.workspace_dir = self.paths.workspace_dir
        self.sessions_dir = self.paths.sessions_dir
        self.memory_dir = self.paths.memory_dir
        self.logs_dir = self.paths.logs_dir
        self.skills_dir = self.paths.skills_dir

        provider_name = self.provider_name or self.provider.name or "openai_compat"
        provider_name = provider_name.strip().lower().replace("-", "_")
        self.provider.name = provider_name
        if self.openai_api_key is not None:
            self.provider.api_key = self.openai_api_key
        if self.openai_api_base is not None:
            self.provider.api_base = self.openai_api_base
        if self.model_name is not None:
            self.provider.model = self.model_name
        if not self.provider.backend.strip():
            self.provider.backend = self.provider.name
        self.provider_name = self.provider.name
        self.openai_api_key = self.provider.api_key
        self.openai_api_base = self.provider.api_base
        self.model_name = self.provider.model

        if self.max_iterations is not None:
            self.agent.max_iterations = int(self.max_iterations)
        if self.max_concurrent_subagents is not None:
            self.agent.max_concurrent_subagents = int(self.max_concurrent_subagents)
        if self.max_tool_result_chars is not None:
            self.agent.max_tool_result_chars = int(self.max_tool_result_chars)
        self.agent.name = self.name
        self.agent.identity = self.identity
        self.max_iterations = int(self.agent.max_iterations)
        self.max_concurrent_subagents = int(self.agent.max_concurrent_subagents)
        self.max_tool_result_chars = int(self.agent.max_tool_result_chars)
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        env_file = getattr(dotenv_settings, "env_file", settings_cls.model_config.get("env_file"))
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _JsonConfigSettingsSource(settings_cls, init_settings=init_settings, env_file=env_file),
            file_secret_settings,
        )

    @property
    def resolved_config_path(self) -> Path:
        """Return the config.json path used by this settings object."""
        env_file = self.model_config.get("env_file")
        return resolve_config_path(
            config_path=self.config_path,
            home_dir=self.home_dir,
            env_file=env_file,
        )

    def to_config(self) -> ZingClawConfig:
        """Convert runtime settings into a structured config document."""
        return ZingClawConfig(
            name=self.name,
            identity=self.identity,
            home_dir=self.home_dir,
            paths=self.paths.model_copy(deep=True),
            agent=self.agent.model_copy(deep=True),
            provider=self.provider.model_copy(deep=True),
            channels=[channel.model_copy(deep=True) for channel in self.channels],
            mcp_servers=[server.model_copy(deep=True) for server in self.mcp_servers],
            memory=self.memory.model_copy(deep=True),
            logging=self.logging.model_copy(deep=True),
            api=self.api.model_copy(deep=True),
            webui=self.webui.model_copy(deep=True),
            security=self.security.model_copy(deep=True),
        )


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
        settings.skills_dir,
    ):
        assert path is not None
        path.mkdir(parents=True, exist_ok=True)
