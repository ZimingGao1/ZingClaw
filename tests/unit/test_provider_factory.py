from __future__ import annotations

import pytest

from zingclaw.agent.loop import AgentLoop
from zingclaw.config import Settings
from zingclaw.providers import OpenAICompatProvider, create_provider


def test_provider_factory_builds_openai_compat_provider_from_settings(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        home_dir=tmp_path,
        provider={
            "name": "openai_compat",
            "model": "kimi-k2.5",
            "api_key": "test-key",
            "api_base": "https://api.moonshot.cn/v1",
            "temperature": 0.2,
            "max_tokens": 2048,
            "reasoning_effort": "medium",
        },
    )

    provider = create_provider(settings)

    assert isinstance(provider, OpenAICompatProvider)
    assert provider.api_key == "test-key"
    assert provider.api_base == "https://api.moonshot.cn/v1"
    assert provider.default_model == "kimi-k2.5"
    assert provider.extra_headers == {}
    assert provider.generation.temperature == 0.2
    assert provider.generation.max_tokens == 2048
    assert provider.generation.reasoning_effort == "medium"


def test_provider_factory_uses_provider_name_alias_from_settings(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        home_dir=tmp_path,
        provider_name="openai-compat",
        openai_api_key="test-key",
        model_name="kimi-k2.5",
    )

    provider = create_provider(settings)

    assert isinstance(provider, OpenAICompatProvider)
    assert provider.api_key == "test-key"
    assert provider.get_default_model() == "kimi-k2.5"


def test_provider_factory_rejects_unknown_provider_name(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        home_dir=tmp_path,
        provider_name="not-real",
    )

    with pytest.raises(ValueError, match="Unknown provider 'not_real'"):
        create_provider(settings)


def test_agent_loop_from_settings_builds_provider_via_factory(tmp_path, monkeypatch) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    sentinel = OpenAICompatProvider(
        api_key="sentinel-key",
        api_base="https://example.com/v1",
        default_model="sentinel-model",
    )

    def fake_create_provider(passed_settings: Settings):
        assert passed_settings is settings
        return sentinel

    monkeypatch.setattr("zingclaw.agent.loop.create_provider", fake_create_provider)

    loop = AgentLoop.from_settings(settings)

    assert loop.provider is sentinel
