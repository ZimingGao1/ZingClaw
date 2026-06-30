"""Provider registry for ZingClaw LLM backends."""

from __future__ import annotations

from dataclasses import dataclass

from zingclaw.providers.base import LLMProvider
from zingclaw.providers.openai_compat import OpenAICompatProvider


def _normalize_provider_name(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Metadata describing one available provider backend."""

    name: str
    backend: str
    provider_class: type[LLMProvider]
    display_name: str = ""
    aliases: tuple[str, ...] = ()
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_reasoning: bool = False
    default_api_base: str | None = None
    default_model: str | None = None
    default_extra_headers: tuple[tuple[str, str], ...] = ()

    @property
    def label(self) -> str:
        return self.display_name or self.name

    def matches_name(self, name: str) -> bool:
        normalized = _normalize_provider_name(name)
        if normalized == self.name:
            return True
        return normalized in {_normalize_provider_name(alias) for alias in self.aliases}


_BUILTIN_PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="openai_compat",
        backend="openai_compat",
        provider_class=OpenAICompatProvider,
        display_name="OpenAI-Compatible",
        aliases=("openai-compat", "openai compatible"),
        supports_streaming=True,
        supports_tools=True,
        supports_reasoning=True,
    ),
)


def list_provider_specs() -> tuple[ProviderSpec, ...]:
    """Return all built-in provider specs."""
    return _BUILTIN_PROVIDER_SPECS


def provider_names() -> tuple[str, ...]:
    """Return all canonical provider names."""
    return tuple(spec.name for spec in _BUILTIN_PROVIDER_SPECS)


def find_provider_spec(name: str | None) -> ProviderSpec | None:
    """Find a provider spec by canonical name or alias."""
    if name is None:
        return None

    normalized = _normalize_provider_name(name)
    for spec in _BUILTIN_PROVIDER_SPECS:
        if spec.matches_name(normalized):
            return spec
    return None


def require_provider_spec(name: str) -> ProviderSpec:
    """Return a provider spec or raise a clear error."""
    spec = find_provider_spec(name)
    if spec is not None:
        return spec

    available = ", ".join(provider_names())
    raise ValueError(f"Unknown provider '{name}'. Available providers: {available}")
