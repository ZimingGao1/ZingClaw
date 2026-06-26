"""OpenAI-compatible provider used for ZingClaw's first P0 milestone."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from zingclaw.providers.base import (
    LLMProvider,
    LLMResponse,
    TextDeltaCallback,
    ToolCallRequest,
    parse_tool_arguments,
    tool_arguments_json_for_replay,
)

logger = logging.getLogger(__name__)

AsyncOpenAI: Any = None

_ALLOWED_MESSAGE_KEYS = frozenset(
    {"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content", "partial"}
)
_OPENAI_COMPAT_TIMEOUT_S = 120.0


@dataclass(slots=True)
class _ToolCallBuffer:
    id: str = ""
    name: str = ""
    arguments: str = ""


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _normalize_api_host(api_base: str | None) -> str:
    if not api_base:
        return ""
    parsed = urlparse(api_base)
    return (parsed.netloc or parsed.path).lower()


class OpenAICompatProvider(LLMProvider):
    """Minimal OpenAI-compatible chat provider."""

    _KIMI_FIXED_TEMPERATURE_PREFIXES = ("kimi-k2.5", "kimi-k2.6", "kimi-k2.7")

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "gpt-4.1-mini",
        extra_headers: dict[str, str] | None = None,
    ):
        super().__init__(api_key=api_key, api_base=api_base)
        self.default_model = default_model
        self.extra_headers = dict(extra_headers or {})
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()
        self._api_host = _normalize_api_host(api_base)

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client

            global AsyncOpenAI
            if AsyncOpenAI is None:
                from openai import AsyncOpenAI as _AsyncOpenAI

                AsyncOpenAI = _AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.api_key or "no-key",
                base_url=self.api_base,
                default_headers=self.extra_headers or None,
                max_retries=0,
                timeout=_OPENAI_COMPAT_TIMEOUT_S,
            )
            return self._client

    def _sanitize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sanitized: list[dict[str, Any]] = []
        for message in messages:
            clean = {key: value for key, value in message.items() if key in _ALLOWED_MESSAGE_KEYS}

            if clean.get("role") == "assistant" and isinstance(clean.get("tool_calls"), list):
                normalized_calls: list[dict[str, Any]] = []
                for index, tool_call in enumerate(clean["tool_calls"]):
                    normalized_calls.append(self._normalize_tool_call(tool_call, index=index))
                clean["tool_calls"] = normalized_calls
                clean.setdefault("content", None)

            if clean.get("role") == "tool" and clean.get("content") is None:
                clean["content"] = ""

            sanitized.append(clean)
        return sanitized

    def _normalize_tool_call(self, tool_call: Any, *, index: int) -> dict[str, Any]:
        function = _get(tool_call, "function") or {}
        call_id = str(_get(tool_call, "id") or f"call_{index}")
        name = str(_get(function, "name") or _get(tool_call, "name") or "")
        arguments = _get(function, "arguments")
        if arguments is None:
            arguments = _get(tool_call, "arguments")
        return {
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": tool_arguments_json_for_replay(arguments),
            },
        }

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        effective_model = model or self.default_model
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": self._sanitize_messages(messages),
            "max_tokens": max(1, int(max_tokens)),
        }
        if self._should_send_temperature(effective_model):
            kwargs["temperature"] = float(temperature)
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return kwargs

    def _is_moonshot_api(self) -> bool:
        return "moonshot.cn" in self._api_host

    def _is_kimi_fixed_temperature_model(self, model: str | None) -> bool:
        model_name = (model or "").strip().lower()
        return model_name.startswith(self._KIMI_FIXED_TEMPERATURE_PREFIXES)

    def _should_send_temperature(self, model: str | None) -> bool:
        # Kimi's K2.x reasoning models on Moonshot reject custom temperature values.
        if self._is_moonshot_api() and self._is_kimi_fixed_temperature_model(model):
            return False
        return True

    def _extract_text_content(self, content: Any) -> str | None:
        if content is None:
            return None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                text = _get(item, "text")
                if text:
                    parts.append(str(text))
            joined = "\n".join(parts).strip()
            return joined or None
        return str(content)

    def _extract_reasoning_content(self, message: Any) -> str | None:
        return self._extract_text_content(_get(message, "reasoning_content"))

    def _parse_usage(self, response: Any) -> dict[str, int]:
        usage = _get(response, "usage")
        if usage is None:
            return {}
        prompt_tokens = int(_get(usage, "prompt_tokens") or 0)
        completion_tokens = int(_get(usage, "completion_tokens") or 0)
        total_tokens = int(_get(usage, "total_tokens") or (prompt_tokens + completion_tokens))
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _parse_tool_calls(self, message: Any) -> list[ToolCallRequest]:
        tool_calls: list[ToolCallRequest] = []
        for raw_tool_call in (_get(message, "tool_calls") or []):
            function = _get(raw_tool_call, "function") or {}
            tool_calls.append(
                ToolCallRequest(
                    id=str(_get(raw_tool_call, "id") or ""),
                    name=str(_get(function, "name") or ""),
                    arguments=parse_tool_arguments(_get(function, "arguments")),
                )
            )

        if not tool_calls:
            legacy_function_call = _get(message, "function_call")
            if legacy_function_call:
                tool_calls.append(
                    ToolCallRequest(
                        id="function_call",
                        name=str(_get(legacy_function_call, "name") or ""),
                        arguments=parse_tool_arguments(_get(legacy_function_call, "arguments")),
                    )
                )

        return tool_calls

    def _parse(self, response: Any) -> LLMResponse:
        choices = _get(response, "choices") or []
        if not choices:
            return LLMResponse(content="", finish_reason="stop", usage=self._parse_usage(response))

        choice = choices[0]
        message = _get(choice, "message") or SimpleNamespace(content="")
        finish_reason = str(_get(choice, "finish_reason") or "stop")
        tool_calls = self._parse_tool_calls(message)
        content = self._extract_text_content(_get(message, "content"))
        reasoning_content = self._extract_reasoning_content(message)

        return LLMResponse(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=self._parse_usage(response),
        )

    def _parse_stream_tool_calls(self, buffers: dict[int, _ToolCallBuffer]) -> list[ToolCallRequest]:
        tool_calls: list[ToolCallRequest] = []
        for index in sorted(buffers):
            buffer = buffers[index]
            tool_calls.append(
                ToolCallRequest(
                    id=buffer.id or f"call_{index}",
                    name=buffer.name,
                    arguments=parse_tool_arguments(buffer.arguments),
                )
            )
        return tool_calls

    async def _parse_stream(
        self,
        stream: Any,
        *,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> LLMResponse:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_buffers: dict[int, _ToolCallBuffer] = {}
        finish_reason = "stop"
        usage: dict[str, int] = {}

        async for chunk in stream:
            parsed_usage = self._parse_usage(chunk)
            if parsed_usage:
                usage = parsed_usage

            choices = _get(chunk, "choices") or []
            if not choices:
                continue

            choice = choices[0]
            delta = _get(choice, "delta") or SimpleNamespace()
            raw_finish_reason = _get(choice, "finish_reason")
            if raw_finish_reason:
                finish_reason = str(raw_finish_reason)

            text_delta = self._extract_text_content(_get(delta, "content"))
            if text_delta:
                content_parts.append(text_delta)
                if on_text_delta is not None:
                    await on_text_delta(text_delta)

            reasoning_delta = self._extract_reasoning_content(delta)
            if reasoning_delta:
                reasoning_parts.append(reasoning_delta)

            for raw_tool_call in (_get(delta, "tool_calls") or []):
                raw_index = _get(raw_tool_call, "index")
                index = int(raw_index) if raw_index is not None else len(tool_call_buffers)
                buffer = tool_call_buffers.setdefault(index, _ToolCallBuffer())
                raw_id = _get(raw_tool_call, "id")
                if raw_id:
                    buffer.id += str(raw_id)
                function = _get(raw_tool_call, "function") or {}
                name = _get(function, "name")
                if name:
                    buffer.name += str(name)
                arguments = _get(function, "arguments")
                if arguments:
                    buffer.arguments += str(arguments)

        content = "".join(content_parts) or None
        reasoning_content = "".join(reasoning_parts) or None
        return LLMResponse(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=self._parse_stream_tool_calls(tool_call_buffers),
            finish_reason=finish_reason,
            usage=usage,
        )

    def _extract_error_metadata(self, exc: Exception) -> dict[str, Any]:
        response = getattr(exc, "response", None)
        status_code = _get(response, "status_code")
        headers = _get(response, "headers")
        body = _get(response, "text")
        if body is None:
            body = getattr(exc, "body", None)

        error_type = None
        error_code = None
        payload: Any = None
        if isinstance(body, str):
            try:
                payload = json.loads(body)
            except Exception:
                payload = None
        elif isinstance(body, dict):
            payload = body
        if isinstance(payload, dict):
            error_obj = payload.get("error")
            if isinstance(error_obj, dict):
                error_type = error_obj.get("type")
                error_code = error_obj.get("code")
            error_type = error_type or payload.get("type")
            error_code = error_code or payload.get("code")

        error_kind = None
        exc_name = type(exc).__name__.lower()
        if "timeout" in exc_name:
            error_kind = "timeout"
        elif "connection" in exc_name:
            error_kind = "connection"

        retry_after = self._extract_retry_after_from_headers(headers)
        should_retry = None
        if isinstance(status_code, int):
            should_retry = status_code in self._RETRYABLE_STATUS_CODES or status_code >= 500
        elif error_kind in self._TRANSIENT_ERROR_KINDS:
            should_retry = True

        return {
            "error_status_code": status_code,
            "error_kind": error_kind,
            "error_type": str(error_type) if error_type is not None else None,
            "error_code": str(error_code) if error_code is not None else None,
            "error_retry_after_s": retry_after,
            "error_should_retry": should_retry,
        }

    def _handle_error(self, exc: Exception) -> LLMResponse:
        logger.debug("OpenAI-compatible provider request failed: {}", exc)
        message = f"Error calling LLM: {exc}"
        retry_after = None
        response = getattr(exc, "response", None)
        if response is not None:
            retry_after = self._extract_retry_after_from_headers(_get(response, "headers"))
        if retry_after is None:
            retry_after = self._extract_retry_after(message)

        return LLMResponse(
            content=message,
            finish_reason="error",
            retry_after=retry_after,
            **self._extract_error_metadata(exc),
        )

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
        del reasoning_effort  # kept for a consistent provider interface
        client = await self._ensure_client()
        try:
            kwargs = self._build_kwargs(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                tool_choice=tool_choice,
            )
            if on_text_delta is not None:
                stream = await client.chat.completions.create(
                    **kwargs,
                    stream=True,
                )
                return await self._parse_stream(stream, on_text_delta=on_text_delta)

            response = await client.chat.completions.create(
                **kwargs
            )
            return self._parse(response)
        except Exception as exc:
            return self._handle_error(exc)

    def get_default_model(self) -> str:
        return self.default_model
