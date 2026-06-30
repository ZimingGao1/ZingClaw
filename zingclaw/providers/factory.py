"""Provider factory for runtime settings."""

from __future__ import annotations

from zingclaw.config.settings import Settings
from zingclaw.providers.base import GenerationSettings, LLMProvider
from zingclaw.providers.registry import ProviderSpec, require_provider_spec


def provider_spec_from_settings(
    settings: Settings,
    *,
    provider_name: str | None = None,
) -> ProviderSpec:
    """Resolve the provider spec implied by runtime settings."""
    return require_provider_spec(provider_name or settings.provider.name or settings.provider_name or "openai_compat")


def create_provider(
    settings: Settings,
    *,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> LLMProvider:
    """Create the configured provider instance."""
    spec = provider_spec_from_settings(settings, provider_name=provider_name)
    effective_model = (
        model_name
        or settings.provider.model
        or settings.model_name
        or spec.default_model
        or "gpt-4.1-mini"
    )
    effective_api_base = settings.provider.api_base or settings.openai_api_base or spec.default_api_base
    extra_headers = dict(spec.default_extra_headers)
    extra_headers.update(settings.provider.extra_headers)

    if spec.backend == "openai_compat":
        provider = spec.provider_class(
            api_key=settings.openai_api_key,
            api_base=effective_api_base,
            default_model=effective_model,
            extra_headers=extra_headers or None,
        )
        provider.generation = GenerationSettings(
            temperature=settings.provider.temperature,
            max_tokens=settings.provider.max_tokens,
            reasoning_effort=settings.provider.reasoning_effort,
        )
        return provider

    raise ValueError(f"Unsupported provider backend '{spec.backend}' for '{spec.name}'")
