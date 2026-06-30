from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from zingclaw.agent.loop import AgentLoop
from zingclaw.config import MCPServerConfig, Settings
from zingclaw.providers import LLMProvider, LLMResponse, ToolCallRequest
from zingclaw.tools import MCPRuntime, ToolRegistry
from zingclaw.tools.mcp import (
    _normalize_schema_for_openai,
    preset_runtime_lines,
    session_extra,
)


class RecordingProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]):
        super().__init__(api_key=None, api_base=None)
        self.responses = list(responses)
        self.requests: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_text_delta=None,
    ) -> LLMResponse:
        del tools, model, max_tokens, temperature, reasoning_effort, tool_choice, on_text_delta
        self.requests.append(deepcopy(messages))
        return self.responses.pop(0)

    def get_default_model(self) -> str:
        return "dummy-model"


def _mcp_server_script() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "mcp_demo_server.py"


def _mcp_http_server_script() -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "mcp_http_demo_server.py"


def _mcp_settings(tmp_path: Path, *, enabled_tools: list[str] | None = None) -> Settings:
    return Settings(
        _env_file=None,
        home_dir=tmp_path,
        mcp_servers=[
            MCPServerConfig(
                name="demo",
                transport="stdio",
                command=sys.executable,
                args=[str(_mcp_server_script())],
                tool_timeout=10,
                enabled_tools=enabled_tools or ["*"],
            )
        ],
    )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(host: str, port: int, *, timeout_s: float = 8.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {host}:{port}")


def test_normalize_schema_for_openai_strips_unsupported_fields_and_fixes_required() -> None:
    normalized = _normalize_schema_for_openai(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": "value",
            "additionalProperties": False,
            "properties": {
                "value": {
                    "type": ["string", "null"],
                    "additionalProperties": False,
                },
                "nested": {
                    "type": "object",
                    "required": ("name",),
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                    },
                },
            },
        }
    )

    assert "$schema" not in normalized
    assert "additionalProperties" not in normalized
    assert normalized["required"] == ["value"]
    assert normalized["properties"]["value"]["type"] == "string"
    assert normalized["properties"]["value"]["nullable"] is True
    assert normalized["properties"]["nested"]["required"] == ["name"]
    assert "additionalProperties" not in normalized["properties"]["nested"]


def test_session_extra_and_preset_runtime_lines_round_trip() -> None:
    metadata = {
        "mcp_presets": [
            {
                "name": "github",
                "display_name": "GitHub",
                "transport": "stdio",
            }
        ]
    }

    persisted = session_extra(metadata)
    lines = preset_runtime_lines(
        persisted,
        configured_server_names={"github"},
        connected_server_names={"github"},
    )

    assert persisted == metadata
    assert len(lines) == 1
    assert "@github (GitHub; transport=stdio; tool_prefix=mcp_github_)." in lines[0]


def test_preset_runtime_lines_report_missing_or_disconnected_servers() -> None:
    metadata = {
        "mcp_presets": [
            {"name": "github", "display_name": "GitHub", "transport": "stdio"},
            {"name": "database", "display_name": "Database", "transport": "http"},
        ]
    }

    lines = preset_runtime_lines(
        metadata,
        configured_server_names={"github"},
        connected_server_names=set(),
    )

    assert "connection is not currently live" in lines[0]
    assert "has not loaded a matching MCP server config yet" in lines[1]


@pytest.mark.asyncio
async def test_mcp_runtime_registers_tool_resource_and_prompt_wrappers(tmp_path: Path) -> None:
    settings = _mcp_settings(tmp_path)
    registry = ToolRegistry()
    runtime = MCPRuntime(settings, workspace=settings.workspace_dir)

    try:
        await runtime.connect_missing_servers(registry)

        assert "mcp_demo_echo" in registry.tool_names
        assert "mcp_demo_shout" in registry.tool_names
        assert "mcp_demo_resource_greeting" in registry.tool_names
        assert "mcp_demo_prompt_review_plan" in registry.tool_names
        assert any("MCP Server: demo (connected;" in line for line in runtime.runtime_lines())
        statuses = runtime.status()
        assert statuses[0].tool_count == 2
        assert statuses[0].resource_count == 1
        assert statuses[0].prompt_count == 1
        assert statuses[0].capability_count == 4

        echo_result = await registry.execute("mcp_demo_echo", {"value": "ok"})
        resource_result = await registry.execute("mcp_demo_resource_greeting", {})
        prompt_result = await registry.execute(
            "mcp_demo_prompt_review_plan",
            {"topic": "MCP runtime"},
        )

        assert echo_result == "echo:ok"
        assert resource_result == "hello from resource"
        assert "Review topic: MCP runtime" in prompt_result
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_mcp_runtime_honors_enabled_tools_filter(tmp_path: Path) -> None:
    settings = _mcp_settings(tmp_path, enabled_tools=["echo"])
    registry = ToolRegistry()
    runtime = MCPRuntime(settings, workspace=settings.workspace_dir)

    try:
        await runtime.connect_missing_servers(registry)

        assert "mcp_demo_echo" in registry.tool_names
        assert "mcp_demo_shout" not in registry.tool_names
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_agent_loop_executes_mcp_tool_and_injects_runtime_lines(tmp_path: Path) -> None:
    provider = RecordingProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="mcp_demo_echo",
                        arguments={"value": "ping"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(content="done", finish_reason="stop"),
        ]
    )
    settings = _mcp_settings(tmp_path)
    loop = AgentLoop(settings=settings, provider=provider, tools=ToolRegistry())

    try:
        result = await loop.process_message(
            "use the demo MCP server",
            session_key="cli:demo",
            channel="cli",
            chat_id="demo",
        )
    finally:
        await loop.aclose()

    session = loop.sessions.get_or_create("cli:demo")

    assert result.final_content == "done"
    assert result.tools_used == ["mcp_demo_echo"]
    assert "MCP Server: demo (connected;" in provider.requests[0][1]["content"]
    assert session.messages[1]["tool_calls"][0]["function"]["name"] == "mcp_demo_echo"
    assert session.messages[2]["content"] == "echo:ping"


@pytest.mark.asyncio
async def test_agent_loop_persists_mcp_presets_on_session_and_reuses_runtime_lines(tmp_path: Path) -> None:
    provider = RecordingProvider(
        [
            LLMResponse(content="first", finish_reason="stop"),
            LLMResponse(content="second", finish_reason="stop"),
        ]
    )
    settings = _mcp_settings(tmp_path)
    loop = AgentLoop(settings=settings, provider=provider, tools=ToolRegistry())

    try:
        await loop.process_message(
            "first turn",
            session_key="cli:demo",
            channel="cli",
            chat_id="demo",
            metadata={
                "mcp_presets": [
                    {
                        "name": "demo",
                        "display_name": "Demo MCP",
                        "transport": "stdio",
                    }
                ]
            },
        )
        await loop.process_message(
            "second turn",
            session_key="cli:demo",
            channel="cli",
            chat_id="demo",
        )
    finally:
        await loop.aclose()

    session = loop.sessions.get_or_create("cli:demo")

    assert session.metadata["mcp_presets"][0]["name"] == "demo"
    assert "MCP Preset Attachment: @demo (Demo MCP; transport=stdio; tool_prefix=mcp_demo_)." in provider.requests[0][1]["content"]
    assert "MCP Preset Attachment: @demo (Demo MCP; transport=stdio; tool_prefix=mcp_demo_)." in provider.requests[1][3]["content"]


@pytest.mark.asyncio
async def test_mcp_runtime_reload_reconciles_registered_tools(tmp_path: Path) -> None:
    initial = _mcp_settings(tmp_path, enabled_tools=["echo"])
    updated = _mcp_settings(tmp_path, enabled_tools=["shout"])
    registry = ToolRegistry()
    runtime = MCPRuntime(initial, workspace=initial.workspace_dir)

    try:
        await runtime.connect_missing_servers(registry)
        assert "mcp_demo_echo" in registry.tool_names
        assert "mcp_demo_shout" not in registry.tool_names

        result = await runtime.reload(updated, registry=registry)

        assert result["ok"] is True
        assert result["changed"] == ["demo"]
        assert "mcp_demo_echo" not in registry.tool_names
        assert "mcp_demo_shout" in registry.tool_names
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_mcp_runtime_supports_streamable_http_transport(tmp_path: Path) -> None:
    host = "127.0.0.1"
    port = _find_free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(_mcp_http_server_script()),
            "--transport",
            "streamable-http",
            "--host",
            host,
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(host, port)
        settings = Settings(
            _env_file=None,
            home_dir=tmp_path,
            mcp_servers=[
                MCPServerConfig(
                    name="demo_http",
                    transport="streamable_http",
                    url=f"http://{host}:{port}/mcp",
                    tool_timeout=10,
                )
            ],
        )
        registry = ToolRegistry()
        runtime = MCPRuntime(settings, workspace=settings.workspace_dir)
        try:
            await runtime.connect_missing_servers(registry)

            assert "mcp_demo_http_echo" in registry.tool_names
            assert "mcp_demo_http_resource_greeting" in registry.tool_names
            assert "mcp_demo_http_prompt_review_plan" in registry.tool_names
            assert await registry.execute("mcp_demo_http_echo", {"value": "http"}) == "echo:http"
            assert (
                await registry.execute("mcp_demo_http_resource_greeting", {})
            ) == "hello from http resource"
        finally:
            await runtime.aclose()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.asyncio
async def test_mcp_runtime_supports_sse_transport(tmp_path: Path) -> None:
    host = "127.0.0.1"
    port = _find_free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(_mcp_http_server_script()),
            "--transport",
            "sse",
            "--host",
            host,
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(host, port)
        settings = Settings(
            _env_file=None,
            home_dir=tmp_path,
            mcp_servers=[
                MCPServerConfig(
                    name="demo_sse",
                    transport="sse",
                    url=f"http://{host}:{port}/sse",
                    tool_timeout=10,
                )
            ],
        )
        registry = ToolRegistry()
        runtime = MCPRuntime(settings, workspace=settings.workspace_dir)
        try:
            await runtime.connect_missing_servers(registry)

            assert "mcp_demo_sse_echo" in registry.tool_names
            assert "mcp_demo_sse_resource_greeting" in registry.tool_names
            assert "mcp_demo_sse_prompt_review_plan" in registry.tool_names
            assert await registry.execute("mcp_demo_sse_echo", {"value": "sse"}) == "echo:sse"
            assert (
                await registry.execute("mcp_demo_sse_resource_greeting", {})
            ) == "hello from http resource"
        finally:
            await runtime.aclose()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.asyncio
async def test_agent_loop_reload_mcp_reads_updated_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "homeDir": str(tmp_path / "home"),
                "mcpServers": [
                    {
                        "name": "demo",
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [str(_mcp_server_script())],
                        "enabledTools": ["echo"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(_env_file=None, config_path=config_path)
    loop = AgentLoop(settings=settings, provider=RecordingProvider([]), tools=ToolRegistry())

    try:
        statuses = await loop.get_mcp_status()
        assert statuses[0].connected is True
        assert "mcp_demo_echo" in loop.tools.tool_names
        assert "mcp_demo_shout" not in loop.tools.tool_names

        config_path.write_text(
            json.dumps(
                {
                    "homeDir": str(tmp_path / "home"),
                    "mcpServers": [
                        {
                            "name": "demo",
                            "transport": "stdio",
                            "command": sys.executable,
                            "args": [str(_mcp_server_script())],
                            "enabledTools": ["shout"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = await loop.reload_mcp()

        assert result["ok"] is True
        assert result["changed"] == ["demo"]
        assert "mcp_demo_echo" not in loop.tools.tool_names
        assert "mcp_demo_shout" in loop.tools.tool_names
    finally:
        await loop.aclose()
