"""Agent loop that wires session, context, tools, and runner together."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from zingclaw.agent.context import AgentContext, ContextBuilder
from zingclaw.agent.runner import AgentRunResult, AgentRunSpec, AgentRunner
from zingclaw.config import Settings
from zingclaw.providers import LLMProvider, OpenAICompatProvider, TextDeltaCallback
from zingclaw.session import Session, SessionManager
from zingclaw.tools import ToolContext, ToolLoader, ToolRegistry

_DEFAULT_CHANNEL = "cli"


@dataclass(slots=True)
class AgentLoopResult:
    """Persisted outcome of one loop turn."""

    session_key: str
    final_content: str | None
    stop_reason: str
    error: str | None
    messages: list[dict[str, Any]]
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class AgentLoop:
    """Minimal P0 loop for one-turn processing."""

    def __init__(
        self,
        settings: Settings,
        provider: LLMProvider | None = None,
        *,
        session_manager: SessionManager | None = None,
        tools: ToolRegistry | None = None,
        tool_loader: ToolLoader | None = None,
        context_builder: ContextBuilder | None = None,
        runner: AgentRunner | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider or self._build_provider(settings)
        self.sessions = session_manager or SessionManager(settings.sessions_dir)
        self.tools = tools or ToolRegistry()
        self.tool_loader = tool_loader or ToolLoader()

        if tools is None:
            self.tool_loader.load(ToolContext.from_settings(settings), self.tools, scope="core")

        self.context = context_builder or ContextBuilder(
            settings,
            self.sessions,
            self.tools,
            workspace=settings.workspace_dir,
        )
        self.runner = runner or AgentRunner(self.provider)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        provider: LLMProvider | None = None,
    ) -> "AgentLoop":
        """Build a loop from runtime settings."""
        return cls(settings=settings, provider=provider)

    @staticmethod
    def _build_provider(settings: Settings) -> LLMProvider:
        return OpenAICompatProvider(
            api_key=settings.openai_api_key,
            api_base=settings.openai_api_base,
            default_model=settings.model_name,
        )

    @property
    def tool_names(self) -> list[str]:
        return self.tools.tool_names

    async def process_message(
        self,
        message: str,
        *,
        session_key: str = "default",
        channel: str = _DEFAULT_CHANNEL,
        chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        current_role: str = "user",
        extra_runtime_lines: Sequence[str] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        error_message: str | None = None,
        max_iterations_message: str | None = None,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> AgentLoopResult:
        """Persist one inbound turn, run the agent, and save the outcome."""
        meta = dict(metadata or {})
        resolved_chat_id = chat_id or session_key

        session = self.sessions.get_or_create(session_key)
        self._persist_inbound_message(
            session,
            role=current_role,
            content=message,
            channel=channel,
            chat_id=resolved_chat_id,
            metadata=meta,
        )
        self.sessions.save(session)

        ctx = self._build_turn_context(
            session=session,
            session_key=session_key,
            current_message=message,
            current_role=current_role,
            channel=channel,
            chat_id=resolved_chat_id,
            metadata=meta,
            extra_runtime_lines=extra_runtime_lines,
        )

        run_result = await self.runner.run(
            AgentRunSpec.from_context(
                ctx,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                tool_choice=tool_choice,
                error_message=error_message,
                max_iterations_message=max_iterations_message,
                on_text_delta=on_text_delta,
            )
        )

        persisted_messages = self._persist_runner_messages(session, ctx, run_result)
        self.sessions.save(session)

        return AgentLoopResult(
            session_key=session_key,
            final_content=run_result.final_content,
            stop_reason=run_result.stop_reason,
            error=run_result.error,
            messages=persisted_messages,
            tools_used=list(run_result.tools_used),
            usage=dict(run_result.usage),
        )

    def _build_turn_context(
        self,
        *,
        session: Session,
        session_key: str,
        current_message: str,
        current_role: str,
        channel: str,
        chat_id: str,
        metadata: dict[str, Any],
        extra_runtime_lines: Sequence[str] | None = None,
    ) -> AgentContext:
        history = session.get_history(
            max_messages=120,
            max_tokens=0,
            include_timestamps=True,
            extend_to_user=current_role != "user",
        )
        messages = self.context.build_messages(
            history=history,
            current_message="",
            current_role=current_role,
            tools=self.tools,
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            workspace=self.settings.workspace_dir,
            metadata=metadata,
            extra_runtime_lines=extra_runtime_lines,
        )
        return AgentContext(
            session_key=session_key,
            session=session,
            history=history,
            messages=messages,
            tools=self.tools,
            tool_definitions=self.tools.get_definitions(),
            workspace=self.settings.workspace_dir,
            current_message=current_message,
            current_role=current_role,
            model=self.settings.model_name,
            max_iterations=self.settings.max_iterations,
            max_tool_result_chars=self.settings.max_tool_result_chars,
            channel=channel,
            chat_id=chat_id,
            metadata=dict(metadata),
        )

    @staticmethod
    def _persist_inbound_message(
        session: Session,
        *,
        role: str,
        content: Any,
        channel: str,
        chat_id: str,
        metadata: dict[str, Any],
    ) -> None:
        extra = {
            key: value
            for key, value in metadata.items()
            if key not in {"content", "role"} and value is not None
        }
        extra["channel"] = channel
        extra["chat_id"] = chat_id
        session.add_message(role, content, **extra)

    def _persist_runner_messages(
        self,
        session: Session,
        ctx: AgentContext,
        run_result: AgentRunResult,
    ) -> list[dict[str, Any]]:
        messages = deepcopy(run_result.messages)
        appended = messages[1 + len(ctx.history) :]

        for message in appended:
            self._append_message_dict(session, message)

        synthetic_final = self._needs_synthetic_final_message(run_result, appended)
        if synthetic_final is not None:
            self._append_message_dict(session, synthetic_final)
            messages.append(deepcopy(synthetic_final))

        return messages

    @staticmethod
    def _needs_synthetic_final_message(
        run_result: AgentRunResult,
        appended: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not run_result.final_content:
            return None
        if run_result.stop_reason not in {"error", "max_iterations"}:
            return None
        if appended:
            last = appended[-1]
            if last.get("role") == "assistant" and last.get("content") == run_result.final_content:
                return None
        return {"role": "assistant", "content": run_result.final_content}

    @staticmethod
    def _append_message_dict(session: Session, message: dict[str, Any]) -> None:
        payload = deepcopy(message)
        role = str(payload.pop("role", "assistant") or "assistant")
        content = payload.pop("content", "")
        payload.pop("timestamp", None)
        session.add_message(role, content, **payload)

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.sessions.list_sessions()

    def read_session(self, key: str) -> dict[str, Any] | None:
        return self.sessions.read_session_file(key)

    def delete_session(self, key: str) -> bool:
        return self.sessions.delete_session(key)

    async def aclose(self) -> None:
        """Flush session state for graceful shutdown."""
        self.sessions.flush_all()

    def close(self) -> None:
        """Synchronous wrapper for graceful shutdown."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        raise RuntimeError("AgentLoop.close() cannot be called from a running event loop; use aclose().")
