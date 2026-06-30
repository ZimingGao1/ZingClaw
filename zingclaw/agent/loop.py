"""Agent loop that wires session, context, tools, and runner together."""

from __future__ import annotations

import asyncio
import logging
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from zingclaw.agent.context import AgentContext, ContextBuilder
from zingclaw.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from zingclaw.agent.subagent import SubagentManager
from zingclaw.bus import (
    InboundMessage,
    MessageBus,
    OutboundMessage,
    RuntimeEventBus,
    RuntimeEventHook,
    SessionTurnStarted,
    TurnCompleted,
    TurnRunStatusChanged,
    build_runtime_event_context,
)
from zingclaw.config import Settings
from zingclaw.cron import CronService
from zingclaw.memory import Consolidator, MemoryStore
from zingclaw.providers import LLMProvider, TextDeltaCallback, create_provider
from zingclaw.session import Session, SessionManager, should_persist_user_message
from zingclaw.tools import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_ACK,
    RUNTIME_CONTROL_MCP_RELOAD,
    MCPRuntime,
    MCPServerStatus,
    ToolContext,
    ToolLoader,
    ToolRegistry,
    preset_runtime_lines,
    session_extra,
)

_DEFAULT_CHANNEL = "cli"
logger = logging.getLogger(__name__)


def _default_timezone_name() -> str:
    tzinfo = datetime.now().astimezone().tzinfo
    key = getattr(tzinfo, "key", None)
    return str(key or "UTC")


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
        bus: MessageBus | None = None,
        runtime_events: RuntimeEventBus | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider or self._build_provider(settings)
        self.sessions = session_manager or SessionManager(settings.sessions_dir)
        self.tools = tools or ToolRegistry()
        self.tool_loader = tool_loader or ToolLoader()
        self.bus = bus
        self.timezone = _default_timezone_name()
        self.mcp = MCPRuntime(settings, workspace=settings.workspace_dir)
        self.cron = CronService(
            settings.workspace_dir / "cron" / "jobs.json",
            self.sessions,
            default_timezone=self.timezone,
        )
        self.subagents = SubagentManager(
            settings,
            self.sessions,
            provider_factory=lambda: self._build_provider(settings),
            max_concurrent_subagents=settings.agent.max_concurrent_subagents,
        )

        if tools is None:
            self.tool_loader.load(
                ToolContext.from_settings(
                    settings,
                    sessions=self.sessions,
                    bus=self.bus,
                    subagent_manager=self.subagents,
                    cron_service=self.cron,
                    mcp_runtime=self.mcp,
                    timezone=self.timezone,
                ),
                self.tools,
                scope="core",
            )

        self.memory = MemoryStore(
            settings.memory_dir,
            max_history_entries=settings.memory.max_history_entries,
        )

        self.context = context_builder or ContextBuilder(
            settings,
            self.sessions,
            self.tools,
            workspace=settings.workspace_dir,
        )
        self.runner = runner or AgentRunner(self.provider)
        self.runtime_events = runtime_events or RuntimeEventBus()
        self.consolidator = (
            Consolidator(
                self.memory,
                self.provider,
                model=settings.model_name,
                sessions=self.sessions,
                consolidation_threshold=settings.memory.consolidation_threshold,
            )
            if settings.memory.enabled
            else None
        )
        self._running = False

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        provider: LLMProvider | None = None,
        bus: MessageBus | None = None,
    ) -> "AgentLoop":
        """Build a loop from runtime settings."""
        return cls(settings=settings, provider=provider, bus=bus)

    @staticmethod
    def _build_provider(settings: Settings) -> LLMProvider:
        return create_provider(settings)

    @property
    def tool_names(self) -> list[str]:
        return self.tools.tool_names

    def bind_bus(self, bus: MessageBus) -> None:
        """Attach a MessageBus used by ``run()`` for inbound/outbound routing."""
        self.bus = bus
        message_tool = self.tools.get("message")
        setter = getattr(message_tool, "set_send_callback", None)
        if callable(setter):
            setter(bus.publish_outbound)

    def stop(self) -> None:
        """Request the bus-driven loop to stop after the current poll cycle."""
        self._running = False

    async def run(self) -> None:
        """Consume inbound bus messages and publish outbound responses."""
        if self.bus is None:
            raise RuntimeError("AgentLoop.run() requires a bound MessageBus")

        self._running = True
        try:
            while self._running:
                try:
                    inbound = await asyncio.wait_for(self.bus.consume_inbound(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

                try:
                    if await self._handle_runtime_control(inbound):
                        continue
                    await self._dispatch_inbound_message(inbound)
                except Exception as exc:
                    logger.exception("AgentLoop bus dispatch failed")
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=inbound.channel,
                            chat_id=inbound.chat_id,
                            session_key=inbound.session_key,
                            text="Sorry, the agent runtime failed processing this message.",
                            reply_to=inbound.message_id,
                            final=True,
                            metadata={
                                "stop_reason": "error",
                                "error": f"Agent loop dispatch failed: {exc}",
                            },
                        )
                    )
        finally:
            self._running = False

    async def _dispatch_inbound_message(self, inbound: InboundMessage) -> AgentLoopResult:
        if self.bus is None:
            raise RuntimeError("AgentLoop bus dispatch requires a bound MessageBus")

        streamed = False
        wants_stream = bool(inbound.metadata.get("_wants_stream"))

        async def _on_text_delta(delta: str) -> None:
            nonlocal streamed
            streamed = True
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    session_key=inbound.session_key,
                    text=delta,
                    reply_to=inbound.message_id,
                    final=False,
                    metadata={"_stream_delta": True},
                )
            )

        result = await self.process_message(
            on_text_delta=_on_text_delta if wants_stream else None,
            **inbound.to_loop_kwargs(),
        )

        await self.bus.publish_outbound(
            self._build_outbound_message(
                inbound,
                result,
                streamed=streamed,
            )
        )
        return result

    async def _handle_runtime_control(self, inbound: InboundMessage) -> bool:
        metadata = inbound.metadata if isinstance(inbound.metadata, dict) else {}
        control = metadata.get(INBOUND_META_RUNTIME_CONTROL)
        if control != RUNTIME_CONTROL_MCP_RELOAD:
            return False

        ack = metadata.get(RUNTIME_CONTROL_ACK)
        try:
            result = await self.reload_mcp()
        except Exception as exc:
            logger.exception("AgentLoop MCP reload failed")
            result = {
                "ok": False,
                "message": "MCP hot reload failed. Restart ZingClaw to recover.",
                "requires_restart": True,
                "error": str(exc),
            }
        if isinstance(ack, asyncio.Future) and not ack.done():
            ack.set_result(result)
        return True

    @staticmethod
    def _build_outbound_message(
        inbound: InboundMessage,
        result: AgentLoopResult,
        *,
        streamed: bool,
    ) -> OutboundMessage:
        metadata: dict[str, Any] = {
            "stop_reason": result.stop_reason,
            "tools_used": list(result.tools_used),
            "usage": dict(result.usage),
        }
        if result.error is not None:
            metadata["error"] = result.error
        if streamed:
            metadata["_stream_end"] = True
            metadata["_streamed"] = True

        return OutboundMessage(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            session_key=inbound.session_key,
            text="" if streamed else (result.final_content or ""),
            reply_to=inbound.message_id,
            final=True,
            metadata=metadata,
        )

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
        """Build a turn from prior history, persist the inbound, then run/save the result."""
        meta = dict(metadata or {})
        resolved_chat_id = chat_id or session_key
        turn_started_at = time.perf_counter()
        runtime_context = build_runtime_event_context(
            channel=channel,
            chat_id=resolved_chat_id,
            session_key=session_key,
            metadata=meta,
        )
        await self.runtime_events.publish(SessionTurnStarted(context=runtime_context))
        await self.runtime_events.publish(
            TurnRunStatusChanged(context=runtime_context, status="building_context")
        )

        session = self.sessions.get_or_create(session_key)
        persisted_mcp = session_extra(meta)
        if persisted_mcp:
            session.metadata.update(persisted_mcp)
        elif "mcp_presets" in meta and not meta.get("mcp_presets"):
            session.metadata.pop("mcp_presets", None)

        if session.restore_pending_user_turn():
            self.sessions.save(session)

        await self.mcp.connect_missing_servers(self.tools)
        runtime_lines = self._merged_mcp_runtime_lines(meta, session.metadata)
        if runtime_lines:
            meta["_mcp_runtime_lines"] = runtime_lines

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
        inbound_persisted = self._persist_current_turn_early(
            session,
            role=current_role,
            content=message,
            channel=channel,
            chat_id=resolved_chat_id,
            metadata=meta,
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
                provider_retry_mode=self.settings.agent.provider_retry_mode,
                on_text_delta=on_text_delta,
                hooks=[
                    RuntimeEventHook(
                        self.runtime_events,
                        channel=channel,
                        chat_id=resolved_chat_id,
                        session_key=session_key,
                        metadata=meta,
                    )
                ],
            )
        )

        persisted_messages = self._persist_runner_messages(session, ctx, run_result)
        if inbound_persisted:
            session.clear_pending_user_turn()
        self.sessions.save(session)
        if self.consolidator is not None:
            await self.consolidator.maybe_consolidate(session)

        await self.runtime_events.publish(
            TurnCompleted(
                context=runtime_context,
                stop_reason=run_result.stop_reason,
                final_content=run_result.final_content,
                tools_used=list(run_result.tools_used),
                usage=dict(run_result.usage),
                error=run_result.error,
                latency_ms=max(0, int((time.perf_counter() - turn_started_at) * 1000)),
            )
        )

        return AgentLoopResult(
            session_key=session_key,
            final_content=run_result.final_content,
            stop_reason=run_result.stop_reason,
            error=run_result.error,
            messages=persisted_messages,
            tools_used=list(run_result.tools_used),
            usage=dict(run_result.usage),
        )

    def _merged_mcp_runtime_lines(
        self,
        message_metadata: dict[str, Any],
        session_metadata: dict[str, Any],
    ) -> list[str]:
        merged_runtime_lines: list[str] = []
        existing_runtime_lines = message_metadata.get("_mcp_runtime_lines")
        if isinstance(existing_runtime_lines, Sequence) and not isinstance(
            existing_runtime_lines,
            (str, bytes, bytearray),
        ):
            merged_runtime_lines.extend(
                str(line)
                for line in existing_runtime_lines
                if str(line).strip()
            )
        merged_runtime_lines.extend(self.mcp.runtime_lines())
        merged_runtime_lines.extend(
            preset_runtime_lines(
                message_metadata if message_metadata.get("mcp_presets") is not None else session_metadata,
                configured_server_names=self.mcp.configured_server_names,
                connected_server_names=self.mcp.connected_server_names,
            )
        )
        deduped: list[str] = []
        seen: set[str] = set()
        for line in merged_runtime_lines:
            key = line.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(key)
        return deduped

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
        ctx = self.context.prepare_turn(
            session_key=session_key,
            current_message=current_message,
            current_role=current_role,
            tools=self.tools,
            channel=channel,
            chat_id=chat_id,
            workspace=self.settings.workspace_dir,
            metadata=metadata,
            history_max_messages=120,
            history_max_tokens=0,
            include_timestamps=True,
            extend_to_user=current_role != "user",
            extra_runtime_lines=extra_runtime_lines,
        )
        return AgentContext(
            session_key=ctx.session_key,
            session=ctx.session,
            history=ctx.history,
            messages=ctx.messages,
            tools=ctx.tools,
            tool_definitions=ctx.tool_definitions,
            workspace=ctx.workspace,
            current_message=ctx.current_message,
            current_media=list(ctx.current_media),
            current_role=current_role,
            model=ctx.model,
            max_iterations=ctx.max_iterations,
            max_tool_result_chars=ctx.max_tool_result_chars,
            channel=ctx.channel,
            chat_id=ctx.chat_id,
            metadata=dict(ctx.metadata),
        )

    def _persist_current_turn_early(
        self,
        session: Session,
        *,
        role: str,
        content: Any,
        channel: str,
        chat_id: str,
        metadata: dict[str, Any],
    ) -> bool:
        if role == "user" and not should_persist_user_message(metadata):
            return False
        if role == "user" and not str(content or "").strip():
            return False
        if role != "user" and content is None:
            return False

        self._persist_inbound_message(
            session,
            role=role,
            content=content,
            channel=channel,
            chat_id=chat_id,
            metadata=metadata,
        )
        if role == "user":
            session.mark_pending_user_turn()
        self.sessions.save(session)
        return True

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
        appended = messages[len(ctx.messages) :]

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

    def drain_subagent_notifications(self, session_key: str) -> list[str]:
        return self.subagents.drain_notifications(session_key)

    def drain_cron_notifications(self, session_key: str) -> list[str]:
        return self.cron.drain_notifications(session_key)

    async def get_mcp_status(self, *, connect_missing: bool = True) -> list[MCPServerStatus]:
        if connect_missing:
            await self.mcp.connect_missing_servers(self.tools)
        return self.mcp.status()

    async def reload_mcp(self) -> dict[str, Any]:
        refreshed_settings = Settings(
            config_path=self.settings.resolved_config_path,
            home_dir=self.settings.home_dir,
        )
        result = await self.mcp.reload(refreshed_settings, registry=self.tools)
        self.settings.mcp_servers = [server.model_copy(deep=True) for server in refreshed_settings.mcp_servers]
        return result

    async def aclose(self) -> None:
        """Flush session state for graceful shutdown."""
        self.stop()
        self.cron.stop(wait=True)
        self.subagents.shutdown(wait=False)
        await self.mcp.aclose()
        self.sessions.flush_all()

    def close(self) -> None:
        """Synchronous wrapper for graceful shutdown."""
        self.stop()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        raise RuntimeError("AgentLoop.close() cannot be called from a running event loop; use aclose().")
