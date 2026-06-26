"""Runtime context passed to tool loaders and tool factories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zingclaw.config import Settings


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Minimal runtime data needed to assemble built-in tools."""

    workspace: Path
    settings: Settings | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "ToolContext":
        return cls(workspace=settings.workspace_dir, settings=settings)

    @property
    def max_tool_result_chars(self) -> int:
        if self.settings is not None:
            return int(self.settings.max_tool_result_chars)
        return 16_000
