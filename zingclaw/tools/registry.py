"""Tool registry for ZingClaw's first P0 runtime slice."""

from __future__ import annotations

import json
from typing import Any

from zingclaw.tools.base import Tool


class ToolRegistry:
    """Register, describe, validate, and execute tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    @staticmethod
    def _lookup_key(name: str) -> str:
        return "".join(ch.lower() for ch in name if ch.isalnum())

    def _suggest_name(self, name: str) -> str | None:
        key = self._lookup_key(str(name or ""))
        if not key:
            return None
        matches = [registered for registered in self._tools if self._lookup_key(registered) == key]
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        function = schema.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        """Return cached tool schemas with stable prompt ordering."""
        if self._cached_definitions is not None:
            return self._cached_definitions

        definitions = [tool.to_schema() for tool in self._tools.values()]
        builtins: list[dict[str, Any]] = []
        mcp_tools: list[dict[str, Any]] = []

        for schema in definitions:
            name = self._schema_name(schema)
            if name.startswith("mcp_"):
                mcp_tools.append(schema)
            else:
                builtins.append(schema)

        builtins.sort(key=self._schema_name)
        mcp_tools.sort(key=self._schema_name)
        self._cached_definitions = builtins + mcp_tools
        return self._cached_definitions

    @classmethod
    def _coerce_argument_value(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, str):
            return value

        stripped = value.strip()
        if not stripped:
            return {}
        if not stripped.startswith(("{", "[")):
            return value

        try:
            return json.loads(stripped)
        except Exception:
            return value

    @classmethod
    def _unwrap_arguments_payload(cls, tool: Tool, params: Any) -> Any:
        if not isinstance(params, dict) or set(params) != {"arguments"}:
            return params
        properties = (tool.parameters or {}).get("properties", {})
        if isinstance(properties, dict) and "arguments" in properties:
            return params
        return cls._coerce_argument_value(params.get("arguments"))

    @classmethod
    def _coerce_params(cls, tool: Tool, params: Any) -> Any:
        params = cls._coerce_argument_value(params)
        return cls._unwrap_arguments_payload(tool, params)

    def prepare_call(self, name: str, params: Any) -> tuple[Tool | None, Any, str | None]:
        """Resolve, cast, and validate one tool call before execution."""
        tool = self._tools.get(name)
        if tool is None:
            suggestion = self._suggest_name(str(name))
            hint = f" Did you mean '{suggestion}'? Tool names must match exactly." if suggestion else ""
            return None, params, (
                f"Error: Tool '{name}' not found.{hint} Available: {', '.join(self.tool_names)}"
            )

        params = self._coerce_params(tool, params)
        if not isinstance(params, dict):
            return tool, params, (
                f"Error: Tool '{name}' parameters must be a JSON object, got "
                f"{type(params).__name__}. Use named parameters like "
                'tool_name(param1="value1", param2="value2") matching the tool schema.'
            )

        cast_params = tool.cast_params(params)
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)

        return tool, cast_params, None

    async def execute(self, name: str, params: Any) -> Any:
        """Execute a tool by name and return either its result or an actionable error string."""
        hint = "\n\n[Analyze the error above and try a different approach.]"
        tool, params, error = self.prepare_call(name, params)
        if error:
            return error + hint

        try:
            assert tool is not None
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + hint
            return result
        except Exception as exc:
            return f"Error executing {name}: {exc}" + hint

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
