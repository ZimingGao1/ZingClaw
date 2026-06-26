"""MCP-facing wrappers for future external tool registration."""

from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any

from zingclaw.tools.base import Tool

_SANITIZE_RE = re.compile(r"_+")


def sanitize_mcp_name(name: str) -> str:
    """Sanitize an MCP-derived tool name for model-provider compatibility."""
    return _SANITIZE_RE.sub("_", re.sub(r"[^a-zA-Z0-9_-]", "_", name))


class MCPToolWrapper(Tool):
    """Wrap one external MCP capability as a normal ZingClaw tool."""

    _plugin_discoverable = False
    _scopes: set[str] = set()

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        parameters: dict[str, Any] | None,
        executor: Any,
        *,
        read_only: bool = False,
    ) -> None:
        self._server_name = server_name
        self._original_name = tool_name
        self._name = sanitize_mcp_name(f"mcp_{server_name}_{tool_name}")
        self._description = description or tool_name
        self._parameters = parameters or {"type": "object", "properties": {}, "required": []}
        self._executor = executor
        self._read_only = read_only

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return self._read_only

    async def execute(self, **kwargs: Any) -> Any:
        result = self._executor(**kwargs)
        if inspect.isawaitable(result):
            return await result
        if isinstance(result, asyncio.Future):
            return await result
        return result
