from __future__ import annotations

from typing import Any

import pytest

from zingclaw.tools import Tool, ToolRegistry


class FakeTool(Tool):
    def __init__(self, name: str, schema: dict[str, Any] | None = None, *, result: Any = None):
        self._name = name
        self._schema = schema or {"type": "object", "properties": {}}
        self._result = result if result is not None else None

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, **kwargs: Any) -> Any:
        if callable(self._result):
            return self._result(**kwargs)
        return kwargs if self._result is None else self._result


def _tool_names(definitions: list[dict[str, Any]]) -> list[str]:
    return [definition.get("function", {}).get("name", "") for definition in definitions]


def test_get_definitions_orders_builtins_then_mcp_tools() -> None:
    registry = ToolRegistry()
    registry.register(FakeTool("mcp_git_status"))
    registry.register(FakeTool("write_file"))
    registry.register(FakeTool("mcp_fs_list"))
    registry.register(FakeTool("read_file"))

    assert _tool_names(registry.get_definitions()) == [
        "read_file",
        "write_file",
        "mcp_fs_list",
        "mcp_git_status",
    ]


def test_prepare_call_rejects_near_miss_tool_name_with_suggestion() -> None:
    registry = ToolRegistry()
    registry.register(FakeTool("read_file"))

    tool, params, error = registry.prepare_call("readFile", {"path": "foo.txt"})

    assert tool is None
    assert params == {"path": "foo.txt"}
    assert error is not None
    assert "Tool 'readFile' not found" in error
    assert "Did you mean 'read_file'?" in error


def test_prepare_call_parses_json_string_and_unwraps_arguments_payload() -> None:
    registry = ToolRegistry()
    registry.register(
        FakeTool(
            "read_file",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
    )

    tool, params, error = registry.prepare_call("read_file", '{"path":"foo.txt"}')
    assert tool is not None
    assert params == {"path": "foo.txt"}
    assert error is None

    tool, params, error = registry.prepare_call(
        "read_file",
        {"arguments": '{"path":"bar.txt"}'},
    )
    assert tool is not None
    assert params == {"path": "bar.txt"}
    assert error is None


def test_prepare_call_rejects_non_object_params() -> None:
    registry = ToolRegistry()
    registry.register(FakeTool("grep"))

    tool, params, error = registry.prepare_call("grep", ["TODO"])

    assert tool is not None
    assert params == ["TODO"]
    assert error is not None
    assert "parameters must be a JSON object" in error
    assert "This tool takes no parameters." in error


@pytest.mark.asyncio
async def test_execute_casts_and_runs_tool() -> None:
    registry = ToolRegistry()
    registry.register(
        FakeTool(
            "count_lines",
            {
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
            },
            result=lambda **kwargs: f"count={kwargs['count']}",
        )
    )

    result = await registry.execute("count_lines", {"count": "3"})

    assert result == "count=3"


@pytest.mark.asyncio
async def test_execute_returns_actionable_validation_error() -> None:
    registry = ToolRegistry()
    registry.register(
        FakeTool(
            "count_lines",
            {
                "type": "object",
                "properties": {"count": {"type": "integer", "minimum": 1}},
                "required": ["count"],
            },
        )
    )

    result = await registry.execute("count_lines", {"count": 0})

    assert "Invalid parameters" in result
    assert "count must be >= 1" in result
    assert "Accepted keys: count." in result
    assert "Analyze the error above" in result
