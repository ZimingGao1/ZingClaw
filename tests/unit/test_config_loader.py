from __future__ import annotations

import json
from pathlib import Path

from zingclaw.config import (
    ChannelConfig,
    MCPServerConfig,
    ProviderConfig,
    Settings,
    ZingClawConfig,
    load_config,
    save_config,
)


def test_load_settings_reads_nested_config_json_and_syncs_flat_fields(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "name": "zingclaw-dev",
                "identity": "Helpful local coding agent",
                "homeDir": str(home_dir),
                "paths": {"workspaceDir": "custom-workspace"},
                "provider": {
                    "name": "openai_compat",
                    "model": "kimi-k2.5",
                    "apiBase": "https://api.moonshot.cn/v1",
                },
                "agent": {
                    "maxIterations": 33,
                    "maxToolResultChars": 9001,
                    "providerRetryMode": "persistent",
                    "disabledSkills": ["memory", "github"],
                },
                "security": {"execSandbox": "bwrap"},
                "memory": {"maxHistoryEntries": 250},
            }
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=None, config_path=config_path)

    assert settings.name == "zingclaw-dev"
    assert settings.identity == "Helpful local coding agent"
    assert settings.home_dir == home_dir.resolve()
    assert settings.workspace_dir == (home_dir / "custom-workspace").resolve()
    assert settings.skills_dir == (home_dir / "skills").resolve()
    assert settings.provider_name == "openai_compat"
    assert settings.model_name == "kimi-k2.5"
    assert settings.openai_api_base == "https://api.moonshot.cn/v1"
    assert settings.max_iterations == 33
    assert settings.max_tool_result_chars == 9001
    assert settings.agent.provider_retry_mode == "persistent"
    assert settings.agent.disabled_skills == ["memory", "github"]
    assert settings.security.exec_sandbox == "bwrap"
    assert settings.memory.max_history_entries == 250


def test_env_vars_override_nested_json_config_values(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "provider": {"name": "openai_compat", "model": "json-model"},
                "agent": {"maxIterations": 12},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_NAME", "env-model")
    monkeypatch.setenv("MAX_ITERATIONS", "44")

    settings = Settings(_env_file=None, config_path=config_path)

    assert settings.model_name == "env-model"
    assert settings.provider.model == "env-model"
    assert settings.max_iterations == 44
    assert settings.agent.max_iterations == 44


def test_load_config_resolves_env_refs_inside_json_document(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("MOONSHOT_API_KEY", "secret-from-env")
    config_path.write_text(
        json.dumps(
            {
                "provider": {
                    "name": "openai_compat",
                    "model": "kimi-k2.5",
                    "apiKey": "${MOONSHOT_API_KEY}",
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path, env_file=None)

    assert config.provider.api_key == "secret-from-env"
    assert config.provider.model == "kimi-k2.5"


def test_save_config_writes_alias_friendly_document_and_round_trips(tmp_path: Path) -> None:
    config = ZingClawConfig(
        home_dir=tmp_path / "home",
        provider=ProviderConfig(
            name="openai_compat",
            model="kimi-k2.5",
            api_base="https://api.moonshot.cn/v1",
        ),
        channels=[ChannelConfig(name="cli", type="cli")],
        mcp_servers=[
            MCPServerConfig(
                name="filesystem",
                command="python",
                args=["-m", "mcp_server"],
                headers={"Authorization": "Bearer token"},
                tool_timeout=45,
                enabled_tools=["read_file", "list_dir"],
            )
        ],
        security={"exec_sandbox": "bwrap"},
    )
    config_path = tmp_path / "saved.json"

    saved_path = save_config(config, config_path, env_file=None)
    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    loaded = load_config(config_path, env_file=None)

    assert saved_path == config_path.resolve()
    assert payload["provider"]["model"] == "kimi-k2.5"
    assert payload["channels"][0]["name"] == "cli"
    assert payload["mcpServers"][0]["name"] == "filesystem"
    assert payload["mcpServers"][0]["headers"]["Authorization"] == "Bearer token"
    assert payload["mcpServers"][0]["toolTimeout"] == 45
    assert payload["mcpServers"][0]["enabledTools"] == ["read_file", "list_dir"]
    assert payload["security"]["execSandbox"] == "bwrap"
    assert loaded.provider.model == "kimi-k2.5"
    assert loaded.channels[0].name == "cli"
    assert loaded.mcp_servers[0].args == ["-m", "mcp_server"]
    assert loaded.mcp_servers[0].headers == {"Authorization": "Bearer token"}
    assert loaded.mcp_servers[0].tool_timeout == 45
    assert loaded.mcp_servers[0].enabled_tools == ["read_file", "list_dir"]
    assert loaded.security.exec_sandbox == "bwrap"


def test_mcp_transport_aliases_normalize_when_loading_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": [
                    {
                        "name": "demo-http",
                        "transport": "streamable-http",
                        "url": "http://127.0.0.1:8766/mcp",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = load_config(config_path, env_file=None)

    assert loaded.mcp_servers[0].transport == "streamable_http"
