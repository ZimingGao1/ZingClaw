"""Minimal LLM provider interfaces for the first ZingClaw milestone."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

try:
    import json_repair
except Exception:  # pragma: no cover - optional until env is refreshed
    json_repair = None

logger = logging.getLogger(__name__)
TextDeltaCallback = Callable[[str], Awaitable[None]]


def parse_tool_arguments(arguments: Any) -> Any:
    """Parse tool arguments returned by a model.

    Empty input becomes a no-arg call. Valid JSON strings become decoded
    Python values. Malformed JSON is preserved so higher layers can surface a
    useful error instead of silently guessing.
    """
    if arguments is None:
        return {}
    if not isinstance(arguments, str):
        return arguments

    stripped = arguments.strip()
    if not stripped:
        return {}

    try:
        return json.loads(stripped)
    except Exception:
        return arguments


def tool_arguments_json_for_replay(arguments: Any) -> str:
    """Return a JSON string for assistant tool-call replay."""
    return json.dumps(tool_arguments_object_for_replay(arguments), ensure_ascii=False)


def tool_arguments_object_for_replay(arguments: Any) -> dict[str, Any]:
    """Return object-shaped arguments for provider history replay.

    This mirrors the reference behavior more closely than normal execution
    parsing: malformed JSON may be repaired because this path is only used to
    shape already-produced conversation history for the provider wire format.
    """
    if arguments is None:
        return {}

    if isinstance(arguments, str):
        stripped = arguments.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except Exception:
            if json_repair is None:
                return {}
            try:
                parsed = json_repair.loads(stripped)
            except Exception:
                return {}
        return parsed if isinstance(parsed, dict) else {}

    return arguments if isinstance(arguments, dict) else {}


@dataclass(slots=True)
class ToolCallRequest:
    """One tool call requested by the model."""

    id: str
    name: str
    arguments: Any

    def to_openai_tool_call(self) -> dict[str, Any]:
        """Serialize to an OpenAI-compatible assistant tool_call payload."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": tool_arguments_json_for_replay(self.arguments),
            },
        }


@dataclass(slots=True)
class LLMResponse:
    """Normalized response returned by a provider."""

    content: str | None
    reasoning_content: str | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    retry_after: float | None = None
    error_status_code: int | None = None
    error_kind: str | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_retry_after_s: float | None = None
    error_should_retry: bool | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def should_execute_tools(self) -> bool:
        if not self.has_tool_calls:
            return False
        return self.finish_reason in ("tool_calls", "function_call", "stop")


@dataclass(frozen=True, slots=True)
class GenerationSettings:
    """Default generation settings threaded through provider calls."""

    temperature: float = 0.7
    max_tokens: int = 4096
    reasoning_effort: str | None = None


class LLMProvider(ABC):
    """Base interface for all ZingClaw model providers."""

    _CHAT_RETRY_DELAYS = (1.0, 2.0, 4.0)
    _PERSISTENT_MAX_DELAY = 60.0
    _PERSISTENT_IDENTICAL_ERROR_LIMIT = 10
    _RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})
    _TRANSIENT_ERROR_KINDS = frozenset({"timeout", "connection"})
    _TRANSIENT_ERROR_MARKERS = (
        "429",
        "rate limit",
        "timeout",
        "timed out",
        "connection",
        "server error",
        "temporarily unavailable",
        "overloaded",
    )
    _SENTINEL = object()

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key
        self.api_base = api_base
        self.generation = GenerationSettings()

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> LLMResponse:
        """Send one non-streaming chat request."""

    @abstractmethod
    def get_default_model(self) -> str:
        """Return the provider's default model identifier."""

    @classmethod
    def _is_transient_error(cls, content: str | None) -> bool:
        text = (content or "").lower()
        return any(marker in text for marker in cls._TRANSIENT_ERROR_MARKERS)

    @classmethod
    def _is_transient_response(cls, response: LLMResponse) -> bool:
        if response.error_should_retry is not None:
            return bool(response.error_should_retry)

        if response.error_status_code is not None:
            status = int(response.error_status_code)
            if status in cls._RETRYABLE_STATUS_CODES or status >= 500:
                return True

        if (response.error_kind or "").strip().lower() in cls._TRANSIENT_ERROR_KINDS:
            return True

        return cls._is_transient_error(response.content)

    @classmethod
    def _to_retry_seconds(cls, value: float, unit: str | None = None) -> float:
        normalized_unit = (unit or "s").lower()
        if normalized_unit in {"ms", "milliseconds"}:
            return max(0.1, value / 1000.0)
        if normalized_unit in {"m", "min", "minutes"}:
            return max(0.1, value * 60.0)
        return max(0.1, value)

    @classmethod
    def _extract_retry_after(cls, content: str | None) -> float | None:
        text = (content or "").lower()
        patterns = (
            r"retry after\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|minutes)?",
            r"try again in\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|minutes)",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            return cls._to_retry_seconds(float(match.group(1)), match.group(2))
        return None

    @classmethod
    def _extract_retry_after_from_headers(cls, headers: Any) -> float | None:
        if not headers:
            return None

        def _header_value(name: str) -> Any:
            if hasattr(headers, "get"):
                value = headers.get(name) or headers.get(name.title())
                if value is not None:
                    return value
            if isinstance(headers, dict):
                for key, value in headers.items():
                    if isinstance(key, str) and key.lower() == name.lower():
                        return value
            return None

        retry_after = _header_value("retry-after")
        if retry_after is None:
            retry_after = _header_value("retry-after-ms")
            if retry_after is not None:
                try:
                    return cls._to_retry_seconds(float(retry_after), "ms")
                except (TypeError, ValueError):
                    return None
            return None

        text = str(retry_after).strip()
        if not text:
            return None
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return cls._to_retry_seconds(float(text), "s")
        try:
            retry_at = parsedate_to_datetime(text)
        except Exception:
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        remaining = (retry_at - datetime.now(retry_at.tzinfo)).total_seconds()
        return max(0.1, remaining)

    @classmethod
    def _retry_delay_for_attempt(
        cls,
        attempt: int,
        response: LLMResponse,
        *,
        persistent: bool,
    ) -> float:
        retry_after = response.error_retry_after_s
        if retry_after is None:
            retry_after = response.retry_after
        if retry_after is None:
            retry_after = cls._extract_retry_after(response.content)
        if retry_after is not None and retry_after > 0:
            return retry_after

        fallback = cls._CHAT_RETRY_DELAYS[min(attempt - 1, len(cls._CHAT_RETRY_DELAYS) - 1)]
        if persistent:
            return min(max(fallback, 1.0), cls._PERSISTENT_MAX_DELAY)
        return fallback

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: object = _SENTINEL,
        temperature: object = _SENTINEL,
        reasoning_effort: object = _SENTINEL,
        tool_choice: str | dict[str, Any] | None = None,
        retry_mode: str = "standard",
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> LLMResponse:
        """Call ``chat`` with a minimal transient error retry policy."""
        if max_tokens is self._SENTINEL or max_tokens is None:
            max_tokens = self.generation.max_tokens
        if temperature is self._SENTINEL or temperature is None:
            temperature = self.generation.temperature
        if reasoning_effort is self._SENTINEL:
            reasoning_effort = self.generation.reasoning_effort

        attempt = 0
        persistent = retry_mode == "persistent"
        last_error_key: str | None = None
        identical_error_count = 0
        emitted_text = False

        async def _wrapped_text_delta(delta: str) -> None:
            nonlocal emitted_text
            emitted_text = True
            if on_text_delta is not None:
                await on_text_delta(delta)

        while True:
            attempt += 1
            response = await self.chat(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=int(max_tokens),
                temperature=float(temperature),
                reasoning_effort=reasoning_effort,
                tool_choice=tool_choice,
                on_text_delta=_wrapped_text_delta if on_text_delta is not None else None,
            )
            if response.finish_reason != "error":
                return response
            if not self._is_transient_response(response):
                return response
            if emitted_text:
                logger.warning(
                    "Not retrying transient model error after partial streamed output: {}",
                    response.content,
                )
                return response

            error_key = ((response.content or "").strip().lower() or None)
            if error_key and error_key == last_error_key:
                identical_error_count += 1
            else:
                last_error_key = error_key
                identical_error_count = 1 if error_key else 0

            if not persistent and attempt >= len(self._CHAT_RETRY_DELAYS) + 1:
                return response

            if persistent and identical_error_count >= self._PERSISTENT_IDENTICAL_ERROR_LIMIT:
                logger.warning(
                    "Stopping persistent retry after {} identical errors: {}",
                    identical_error_count,
                    response.content,
                )
                return response

            delay = self._retry_delay_for_attempt(attempt, response, persistent=persistent)
            if on_retry_wait:
                kind = "persistent retry" if persistent else "retry"
                await on_retry_wait(
                    f"Model request failed, {kind} in {max(1, int(round(delay)))}s "
                    f"(attempt {attempt})."
                )
            await asyncio.sleep(max(0.0, delay))
