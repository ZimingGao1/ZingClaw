"""Runtime context passed to tool loaders and per-call tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from zingclaw.config import Settings

if TYPE_CHECKING:
    from zingclaw.agent.subagent import SubagentManager
    from zingclaw.bus import MessageBus
    from zingclaw.cron import CronService
    from zingclaw.session import SessionManager
    from zingclaw.tools.mcp import MCPRuntime


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Minimal runtime data needed to assemble built-in tools."""

    workspace: Path
    settings: Settings | None = None
    sessions: "SessionManager | None" = None
    bus: "MessageBus | None" = None
    subagent_manager: "SubagentManager | None" = None
    cron_service: "CronService | None" = None
    mcp_runtime: "MCPRuntime | None" = None
    timezone: str = "UTC"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        sessions: "SessionManager | None" = None,
        bus: "MessageBus | None" = None,
        subagent_manager: "SubagentManager | None" = None,
        cron_service: "CronService | None" = None,
        mcp_runtime: "MCPRuntime | None" = None,
        timezone: str = "UTC",
    ) -> "ToolContext":
        return cls(
            workspace=settings.workspace_dir,
            settings=settings,
            sessions=sessions,
            bus=bus,
            subagent_manager=subagent_manager,
            cron_service=cron_service,
            mcp_runtime=mcp_runtime,
            timezone=timezone,
        )

    @property
    def max_tool_result_chars(self) -> int:
        if self.settings is not None:
            return int(self.settings.max_tool_result_chars)
        return 16_000

    @property
    def restrict_to_workspace(self) -> bool:
        if self.settings is not None:
            return bool(self.settings.security.restrict_to_workspace)
        return True


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Per-tool-call routing context from the active agent turn."""

    session_key: str | None = None
    channel: str | None = None
    chat_id: str | None = None
    workspace: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ContextAware(Protocol):
    """Protocol for tools that need per-request runtime context."""

    def set_context(self, ctx: RequestContext) -> Any:
        """Install one request context and return a reset token."""

    def reset_context(self, token: Any) -> None:
        """Restore the previous request context using a reset token."""
