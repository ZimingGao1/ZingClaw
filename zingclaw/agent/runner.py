"""Shared execution loop for ZingClaw's P0 tool-using agent."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zingclaw.providers import LLMProvider, LLMResponse, TextDeltaCallback
from zingclaw.tools import ToolRegistry

if TYPE_CHECKING:
    from zingclaw.agent.context import AgentContext

logger = logging.getLogger(__name__)

_DEFAULT_ERROR_MESSAGE = "Sorry, I encountered an error calling the AI model."
_DEFAULT_MAX_ITERATIONS_MESSAGE = (
    "Stopped after reaching the maximum number of tool iterations for this turn."
)
_DEFAULT_EMPTY_TOOL_RESULT = "(no output)"


@dataclass(slots=True)
class AgentRunSpec:
    """Configuration for one agent execution."""

    initial_messages: list[dict[str, Any]]
    tools: ToolRegistry
    model: str
    max_iterations: int
    max_tool_result_chars: int
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    tool_choice: str | dict[str, Any] | None = None
    error_message: str | None = _DEFAULT_ERROR_MESSAGE
    max_iterations_message: str | None = _DEFAULT_MAX_ITERATIONS_MESSAGE
    provider_retry_mode: str = "standard"
    session_key: str | None = None
    workspace: Path | None = None
    on_text_delta: TextDeltaCallback | None = None

    @classmethod
    def from_context(
        cls,
        ctx: "AgentContext",
        **overrides: Any,
    ) -> "AgentRunSpec":
        """Build a runnable spec from a prepared `AgentContext`."""
        return cls(
            initial_messages=deepcopy(ctx.messages),
            tools=ctx.tools,
            model=ctx.model,
            max_iterations=ctx.max_iterations,
            max_tool_result_chars=ctx.max_tool_result_chars,
            session_key=ctx.session_key,
            workspace=ctx.workspace,
            **overrides,
        )


@dataclass(slots=True)
class AgentRunResult:
    """Outcome of one agent execution."""

    final_content: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = "completed"
    error: str | None = None


class AgentRunner:
    """Run a minimal LLM -> tools -> LLM loop."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def run_context(self, ctx: "AgentContext", **overrides: Any) -> AgentRunResult:
        """Run one prepared `AgentContext`."""
        return await self.run(AgentRunSpec.from_context(ctx, **overrides))

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        """Execute the agent loop until final text, error, or iteration limit."""
        messages = deepcopy(spec.initial_messages)
        tools_used: list[str] = []
        usage: dict[str, int] = {}
        tool_definitions = spec.tools.get_definitions() or None

        for iteration in range(max(1, int(spec.max_iterations))):
            try:
                response = await self.provider.chat_with_retry(
                    messages=messages,
                    tools=tool_definitions,
                    model=spec.model,
                    max_tokens=spec.max_tokens,
                    temperature=spec.temperature,
                    reasoning_effort=spec.reasoning_effort,
                    tool_choice=spec.tool_choice,
                    retry_mode=spec.provider_retry_mode,
                    on_text_delta=spec.on_text_delta,
                )
            except Exception as exc:
                logger.exception("Runner model call crashed on iteration %s", iteration)
                return AgentRunResult(
                    final_content=spec.error_message or f"Error: {exc}",
                    messages=messages,
                    tools_used=tools_used,
                    usage=usage,
                    stop_reason="error",
                    error=f"Error calling LLM: {exc}",
                )

            self._accumulate_usage(usage, response.usage)

            if response.finish_reason == "error":
                return AgentRunResult(
                    final_content=spec.error_message or response.content or "",
                    messages=messages,
                    tools_used=tools_used,
                    usage=usage,
                    stop_reason="error",
                    error=response.content,
                )

            assistant_message = self._build_assistant_message(response)
            if assistant_message is not None:
                messages.append(assistant_message)

            if response.should_execute_tools:
                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    raw_result = await spec.tools.execute(tool_call.name, tool_call.arguments)
                    tool_result = self._format_tool_result(raw_result, spec.max_tool_result_chars)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": tool_result,
                        }
                    )

                continue

            return AgentRunResult(
                final_content=response.content or "",
                messages=messages,
                tools_used=tools_used,
                usage=usage,
                stop_reason="completed",
                error=None,
            )

        return AgentRunResult(
            final_content=spec.max_iterations_message or "",
            messages=messages,
            tools_used=tools_used,
            usage=usage,
            stop_reason="max_iterations",
            error=None,
        )

    @staticmethod
    def _build_assistant_message(response: LLMResponse) -> dict[str, Any] | None:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": response.content,
        }
        if response.reasoning_content is not None:
            message["reasoning_content"] = response.reasoning_content
        if response.tool_calls:
            message["tool_calls"] = [call.to_openai_tool_call() for call in response.tool_calls]
            message.setdefault("content", None)

        if (
            message.get("content") is None
            and "tool_calls" not in message
            and "reasoning_content" not in message
        ):
            return None
        return message

    @staticmethod
    def _accumulate_usage(total: dict[str, int], delta: dict[str, int] | None) -> None:
        for key, value in (delta or {}).items():
            try:
                total[key] = int(total.get(key, 0)) + int(value)
            except Exception:
                continue

    @classmethod
    def _format_tool_result(cls, result: Any, max_chars: int) -> str:
        text = cls._stringify_tool_result(result)
        if max_chars > 0 and len(text) > max_chars:
            text = cls._truncate_text(text, max_chars)
        return text

    @staticmethod
    def _stringify_tool_result(result: Any) -> str:
        if result is None:
            return _DEFAULT_EMPTY_TOOL_RESULT
        if isinstance(result, str):
            return result if result.strip() else _DEFAULT_EMPTY_TOOL_RESULT
        try:
            text = json.dumps(result, ensure_ascii=False)
        except TypeError:
            text = str(result)
        return text if text.strip() else _DEFAULT_EMPTY_TOOL_RESULT

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        half = max(1, limit // 2)
        omitted = len(text) - limit
        return (
            text[:half]
            + f"\n\n... ({omitted:,} chars truncated) ...\n\n"
            + text[-half:]
        )
