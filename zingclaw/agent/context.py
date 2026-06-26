"""Context builder for ZingClaw's P0 agent loop."""

from __future__ import annotations

import platform
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from zingclaw.config import Settings
from zingclaw.session import Session, SessionManager
from zingclaw.tools import ToolRegistry

_DEFAULT_HISTORY_MESSAGES = 120
_RUNTIME_CONTEXT_TAG = "[Runtime Context - metadata only, not instructions]"
_RUNTIME_CONTEXT_END = "[/Runtime Context]"


@dataclass(slots=True)
class AgentContext:
    """Prepared runtime context for a single model turn."""

    session_key: str
    session: Session
    history: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    tools: ToolRegistry
    tool_definitions: list[dict[str, Any]]
    workspace: Path
    current_message: str
    current_role: str = "user"
    model: str = ""
    max_iterations: int = 0
    max_tool_result_chars: int = 0
    channel: str | None = None
    chat_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tools(self) -> bool:
        return bool(self.tool_definitions)


class ContextBuilder:
    """Assemble system prompt, replay history, and current-turn input."""

    BOOTSTRAP_FILES = ("AGENTS.md", "SOUL.md", "USER.md")

    def __init__(
        self,
        settings: Settings,
        sessions: SessionManager,
        tools: ToolRegistry | None = None,
        *,
        workspace: Path | None = None,
        bootstrap_dir: Path | None = None,
        timezone: str | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.tools = tools if tools is not None else ToolRegistry()
        self.workspace = (workspace or settings.workspace_dir).expanduser().resolve()
        self.bootstrap_dir = (bootstrap_dir or settings.home_dir).expanduser().resolve()
        self.timezone = timezone

    def build_system_prompt(
        self,
        *,
        tools: ToolRegistry | None = None,
        workspace: Path | None = None,
        channel: str | None = None,
        session_key: str | None = None,
        extra_instructions: str | None = None,
    ) -> str:
        """Build a minimal but structured system prompt for provider calls."""
        tool_registry = tools if tools is not None else self.tools
        root = (workspace or self.workspace).expanduser().resolve()

        parts = [
            self._identity_block(root, channel=channel, session_key=session_key),
            self._tool_contract_block(),
        ]

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        tool_summary = self._tool_summary(tool_registry)
        if tool_summary:
            parts.append(tool_summary)

        if extra_instructions:
            parts.append(extra_instructions.strip())

        return "\n\n---\n\n".join(part for part in parts if part.strip())

    def build_messages(
        self,
        *,
        history: list[dict[str, Any]],
        current_message: str,
        current_role: str = "user",
        tools: ToolRegistry | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        session_key: str | None = None,
        workspace: Path | None = None,
        metadata: dict[str, Any] | None = None,
        extra_runtime_lines: Sequence[str] | None = None,
        extra_instructions: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the full message list for one LLM call."""
        tool_registry = tools if tools is not None else self.tools
        root = (workspace or self.workspace).expanduser().resolve()
        messages = [
            {
                "role": "system",
                "content": self.build_system_prompt(
                    tools=tool_registry,
                    workspace=root,
                    channel=channel,
                    session_key=session_key,
                    extra_instructions=extra_instructions,
                ),
            },
            *deepcopy(history),
        ]

        runtime_ctx = self._build_runtime_context(
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            workspace=root,
            metadata=metadata,
            supplemental_lines=extra_runtime_lines,
        )
        merged_content = self._merge_message_content(current_message, runtime_ctx)

        if messages and messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged_content)
            messages[-1] = last
            return messages

        messages.append({"role": current_role, "content": merged_content})
        return messages

    def prepare_turn(
        self,
        *,
        session_key: str,
        current_message: str,
        current_role: str = "user",
        tools: ToolRegistry | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        workspace: Path | None = None,
        metadata: dict[str, Any] | None = None,
        history_max_messages: int = _DEFAULT_HISTORY_MESSAGES,
        history_max_tokens: int = 0,
        include_timestamps: bool = True,
        extend_to_user: bool = False,
        extra_runtime_lines: Sequence[str] | None = None,
        extra_instructions: str | None = None,
    ) -> AgentContext:
        """Load the session and build a single-turn context bundle."""
        tool_registry = tools if tools is not None else self.tools
        root = (workspace or self.workspace).expanduser().resolve()
        session = self.sessions.get_or_create(session_key)
        history = session.get_history(
            max_messages=history_max_messages,
            max_tokens=history_max_tokens,
            include_timestamps=include_timestamps,
            extend_to_user=extend_to_user,
        )
        messages = self.build_messages(
            history=history,
            current_message=current_message,
            current_role=current_role,
            tools=tool_registry,
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            workspace=root,
            metadata=metadata,
            extra_runtime_lines=extra_runtime_lines,
            extra_instructions=extra_instructions,
        )
        return AgentContext(
            session_key=session_key,
            session=session,
            history=history,
            messages=messages,
            tools=tool_registry,
            tool_definitions=tool_registry.get_definitions(),
            workspace=root,
            current_message=current_message,
            current_role=current_role,
            model=self.settings.model_name,
            max_iterations=self.settings.max_iterations,
            max_tool_result_chars=self.settings.max_tool_result_chars,
            channel=channel,
            chat_id=chat_id,
            metadata=dict(metadata or {}),
        )

    def _identity_block(
        self,
        workspace: Path,
        *,
        channel: str | None = None,
        session_key: str | None = None,
    ) -> str:
        system = platform.system()
        machine = platform.machine() or "unknown"
        python_version = platform.python_version()

        lines = [
            "# Identity",
            "You are ZingClaw, a transparent local coding agent.",
            f"Model: {self.settings.model_name}",
            f"Workspace: {workspace}",
            f"Runtime: {system} {machine}, Python {python_version}",
        ]
        if channel:
            lines.append(f"Channel: {channel}")
        if session_key:
            lines.append(f"Session: {session_key}")
        return "\n".join(lines)

    @staticmethod
    def _tool_contract_block() -> str:
        return "\n".join(
            [
                "# Tool Contract",
                "- Use tools when you need fresh workspace state or command output.",
                "- Do not invent tool results.",
                "- Prefer the dedicated file tools for reading and editing files.",
                "- Keep file access and shell work inside the configured workspace.",
                "- When a tool fails, use the error to choose a corrected next step.",
            ]
        )

    def _load_bootstrap_files(self) -> str:
        parts: list[str] = []
        for filename in self.BOOTSTRAP_FILES:
            path = self.bootstrap_dir / filename
            if not path.exists() or not path.is_file():
                continue
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"## {filename}\n\n{content}")
        return "\n\n".join(parts)

    @staticmethod
    def _tool_summary(tool_registry: ToolRegistry) -> str:
        definitions = tool_registry.get_definitions()
        if not definitions:
            return "# Available Tools\n\nNo tools are currently registered."

        lines = ["# Available Tools"]
        for definition in definitions:
            function = definition.get("function", {})
            name = function.get("name")
            description = function.get("description")
            if isinstance(name, str) and name:
                desc = description if isinstance(description, str) and description else "No description."
                lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

    def _current_time_str(self) -> str:
        if self.timezone:
            try:
                return datetime.now(ZoneInfo(self.timezone)).isoformat(timespec="seconds")
            except Exception:
                pass
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def _build_runtime_context(
        self,
        *,
        channel: str | None,
        chat_id: str | None,
        session_key: str | None,
        workspace: Path,
        metadata: dict[str, Any] | None = None,
        supplemental_lines: Sequence[str] | None = None,
    ) -> str:
        lines = [
            f"Current Time: {self._current_time_str()}",
            f"Workspace: {workspace}",
        ]
        if session_key:
            lines.append(f"Session: {session_key}")
        if channel:
            lines.append(f"Channel: {channel}")
        if chat_id:
            lines.append(f"Chat ID: {chat_id}")

        for key in ("message_id", "sender_id"):
            value = (metadata or {}).get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"{key}: {value}")

        if supplemental_lines:
            lines.extend(line for line in supplemental_lines if isinstance(line, str) and line.strip())

        return _RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines) + "\n" + _RUNTIME_CONTEXT_END

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            if not left:
                return right
            if not right:
                return left
            return f"{left}\n\n{right}"

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [
                    item if isinstance(item, dict) else {"type": "text", "text": str(item)}
                    for item in value
                ]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)
