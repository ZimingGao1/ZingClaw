"""Structured configuration schema for ZingClaw."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from zingclaw.config.utils import default_home_dir, resolve_runtime_dir


class ConfigSection(BaseModel):
    """Base model shared by nested config sections."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class PathsConfig(ConfigSection):
    """Standard runtime directory layout."""

    workspace_dir: Path | None = None
    sessions_dir: Path | None = None
    memory_dir: Path | None = None
    logs_dir: Path | None = None
    skills_dir: Path | None = None


class AgentConfig(ConfigSection):
    """Agent runtime defaults."""

    name: str = "zingclaw"
    identity: str = ""
    max_iterations: int = 20
    max_concurrent_subagents: int = 1
    max_tool_result_chars: int = 16_000
    session_ttl_minutes: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "idleCompactAfterMinutes",
            "sessionTtlMinutes",
            "session_ttl_minutes",
        ),
        serialization_alias="idleCompactAfterMinutes",
    )
    provider_retry_mode: Literal["standard", "persistent"] = "standard"
    enabled_skills: list[str] = Field(default_factory=list)
    disabled_skills: list[str] = Field(default_factory=list)


class ProviderConfig(ConfigSection):
    """LLM provider selection plus generation defaults."""

    name: str = "openai_compat"
    backend: str = "openai_compat"
    model: str = "openai/gpt-4.1-mini"
    api_key: str | None = Field(default=None, repr=False)
    api_base: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096
    reasoning_effort: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ChannelConfig(ConfigSection):
    """One external channel definition."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    name: str
    type: str
    enabled: bool = True
    supports_streaming: bool = True
    token: str | None = Field(default=None, repr=False)
    app_id: str | None = None
    app_secret: str | None = Field(default=None, repr=False)
    webhook_url: str | None = None


class MCPServerConfig(ConfigSection):
    """One MCP server entry."""

    name: str
    enabled: bool = True
    transport: Literal["stdio", "http", "sse", "streamable_http"] = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    tool_timeout: int = Field(default=30, ge=1, le=600)
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])

    @field_validator("transport", mode="before")
    @classmethod
    def _normalize_transport(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace("-", "_")
        if normalized == "streamablehttp":
            return "streamable_http"
        return normalized


class MemoryConfig(ConfigSection):
    """Memory subsystem defaults."""

    enabled: bool = True
    consolidation_threshold: int = 50
    max_history_entries: int = 100
    recent_keep_messages: int | None = None
    prompt_token_budget: int | None = None


class LoggingConfig(ConfigSection):
    """Logging output configuration."""

    level: str = "INFO"
    json_output: bool = Field(
        default=False,
        validation_alias=AliasChoices("json", "jsonOutput", "json_output"),
        serialization_alias="json",
    )


class ApiConfig(ConfigSection):
    """Future API server configuration."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8000


class WebUIConfig(ConfigSection):
    """Future WebUI runtime configuration."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 3000


class SecurityConfig(ConfigSection):
    """Workspace and execution safety defaults."""

    restrict_to_workspace: bool = True
    exec_sandbox: str = ""


class ZingClawConfig(ConfigSection):
    """Structured top-level configuration document."""

    name: str = "zingclaw"
    identity: str = ""
    home_dir: Path = Field(default_factory=default_home_dir)
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

    @model_validator(mode="after")
    def _normalize_runtime_layout(self) -> "ZingClawConfig":
        home = self.home_dir.expanduser().resolve()
        self.home_dir = home
        self.paths.workspace_dir = resolve_runtime_dir(self.paths.workspace_dir, home, "workspace")
        self.paths.sessions_dir = resolve_runtime_dir(self.paths.sessions_dir, home, "sessions")
        self.paths.memory_dir = resolve_runtime_dir(self.paths.memory_dir, home, "memory")
        self.paths.logs_dir = resolve_runtime_dir(self.paths.logs_dir, home, "logs")
        self.paths.skills_dir = resolve_runtime_dir(self.paths.skills_dir, home, "skills")
        self.provider.name = self.provider.name.strip().lower().replace("-", "_")
        if not self.provider.backend.strip():
            self.provider.backend = self.provider.name
        return self

    def to_document(self) -> dict[str, Any]:
        """Return a JSON-friendly dict using camelCase aliases."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)
