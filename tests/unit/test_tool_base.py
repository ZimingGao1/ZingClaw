from __future__ import annotations

from typing import Any

from zingclaw.tools import Tool, tool_parameters


class SampleTool(Tool):
    @property
    def name(self) -> str:
        return "sample"

    @property
    def description(self) -> str:
        return "sample tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
                "mode": {"type": "string", "enum": ["fast", "full"]},
                "meta": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "flags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["tag"],
                },
            },
            "required": ["query", "count"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 2},
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query", "count"],
        "additionalProperties": False,
    }
)
class DecoratedSampleTool(Tool):
    @property
    def name(self) -> str:
        return "decorated_sample"

    @property
    def description(self) -> str:
        return "decorated sample tool"

    async def execute(self, **kwargs: Any) -> str:
        return f"ok:{kwargs['count']}"


class CastTestTool(Tool):
    def __init__(self, schema: dict[str, Any]) -> None:
        self._schema = schema

    @property
    def name(self) -> str:
        return "cast_test"

    @property
    def description(self) -> str:
        return "cast test tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_tool_parameters_returns_fresh_copy_per_access() -> None:
    tool = DecoratedSampleTool()

    first = tool.parameters
    second = tool.parameters

    assert first == second
    assert first is not second
    assert first["properties"] is not second["properties"]

    first["properties"]["query"]["minLength"] = 99
    assert tool.parameters["properties"]["query"]["minLength"] == 2


def test_cast_params_handles_nested_values() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "enabled": {"type": "boolean"},
                "meta": {
                    "type": "object",
                    "properties": {
                        "port": {"type": "integer"},
                        "flags": {"type": "array", "items": {"type": "integer"}},
                    },
                },
            },
        }
    )

    casted = tool.cast_params(
        {
            "count": "3",
            "enabled": "true",
            "meta": {"port": "8080", "flags": ["1", "2"]},
        }
    )

    assert casted == {
        "count": 3,
        "enabled": True,
        "meta": {"port": 8080, "flags": [1, 2]},
    }


def test_validate_params_reports_missing_and_nested_errors() -> None:
    errors = SampleTool().validate_params(
        {
            "query": "h",
            "count": 2,
            "mode": "slow",
            "meta": {"flags": [1, "ok"]},
        }
    )

    joined = "; ".join(errors)
    assert "query must be at least 2 chars" in joined
    assert "mode must be one of ['fast', 'full']" in joined
    assert "missing required meta.tag" in joined
    assert "meta.flags[0] should be string" in joined


def test_validate_params_rejects_unknown_fields_when_schema_disallows_them() -> None:
    errors = DecoratedSampleTool().validate_params({"query": "hi", "count": 2, "extra": "x"})

    assert errors == ["unexpected parameter extra"]


def test_nullable_type_accepts_none() -> None:
    tool = CastTestTool(
        {
            "type": "object",
            "properties": {"name": {"type": ["string", "null"]}},
        }
    )

    assert tool.cast_params({"name": None}) == {"name": None}
    assert tool.validate_params({"name": None}) == []


def test_tool_format_parameter_hint_lists_declared_keys() -> None:
    tool = DecoratedSampleTool()

    assert tool.parameter_names() == ["count", "query"]
    assert tool.format_parameter_hint() == "Accepted keys: count, query."
