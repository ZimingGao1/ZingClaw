from __future__ import annotations

from typing import Any

import pytest

from zingclaw.config.settings import Settings
from zingclaw.tools import Tool, ToolContext, ToolLoader, ToolRegistry
from zingclaw.tools.mcp import MCPToolWrapper, sanitize_mcp_name


class EnabledTool(Tool):
    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "enabled_tool"

    @property
    def description(self) -> str:
        return "enabled tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        del ctx
        return cls()

    async def execute(self, **kwargs: Any) -> Any:
        return kwargs


class DisabledTool(EnabledTool):
    @property
    def name(self) -> str:
        return "disabled_tool"

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        del ctx
        return False


class SubagentOnlyTool(EnabledTool):
    _scopes = {"subagent"}

    @property
    def name(self) -> str:
        return "subagent_only"


def test_tool_loader_discovers_builtin_tool_classes() -> None:
    discovered_names = {tool_cls.__name__ for tool_cls in ToolLoader().discover()}

    assert {
        "ApplyPatchTool",
        "CancelSubagentTool",
        "CompleteGoalTool",
        "CronTool",
        "ListDirTool",
        "LongTaskTool",
        "ListSubagentsTool",
        "ReadFileTool",
        "WriteFileTool",
        "EditFileTool",
        "ListExecSessionsTool",
        "FindFilesTool",
        "GrepTool",
        "MessageTool",
        "SpawnTool",
        "WebFetchTool",
        "WebSearchTool",
        "WriteStdinTool",
    } <= discovered_names
    assert "MCPToolWrapper" not in discovered_names


def test_tool_loader_loads_only_enabled_tools_for_scope(tmp_path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    ctx = ToolContext.from_settings(settings)
    registry = ToolRegistry()
    loader = ToolLoader(test_classes=[EnabledTool, DisabledTool, SubagentOnlyTool])

    registered = loader.load(ctx, registry, scope="core")

    assert registered == ["enabled_tool"]
    assert registry.tool_names == ["enabled_tool"]


async def _mcp_echo(**kwargs: Any) -> str:
    return f"echo:{kwargs['value']}"


def test_mcp_wrapper_uses_prefixed_sanitized_name_and_runs_executor() -> None:
    wrapper = MCPToolWrapper(
        server_name="git hub",
        tool_name="status/check",
        description="remote tool",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        executor=_mcp_echo,
        read_only=True,
    )

    assert wrapper.name == "mcp_git_hub_status_check"
    assert wrapper.read_only is True
    assert sanitize_mcp_name("mcp git hub/status/check") == "mcp_git_hub_status_check"


@pytest.mark.asyncio
async def test_mcp_wrapper_executes_wrapped_callable() -> None:
    wrapper = MCPToolWrapper(
        server_name="git hub",
        tool_name="status/check",
        description="remote tool",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        executor=_mcp_echo,
    )

    result = await wrapper.execute(value="ok")

    assert result == "echo:ok"
