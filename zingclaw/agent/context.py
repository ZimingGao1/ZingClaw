"""Context builder for ZingClaw's agent loop."""

from __future__ import annotations

import base64
import mimetypes
import platform
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from zingclaw.agent.skills import SkillsLoader
from zingclaw.config import Settings
from zingclaw.memory import MemoryStore
from zingclaw.session import Session, SessionManager, goal_state_runtime_lines
from zingclaw.tools import ToolRegistry
from zingclaw.utils import render_template

_DEFAULT_HISTORY_MESSAGES = 120
_DEFAULT_RECENT_ARCHIVE_ENTRIES = 6
_DEFAULT_RECENT_ARCHIVE_CHARS = 3_000
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
    current_media: list[str] = field(default_factory=list)
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
        self.memory = MemoryStore(
            settings.memory_dir,
            max_history_entries=settings.memory.max_history_entries,
        )
        self.skills = SkillsLoader(
            self.workspace,
            skills_dir=settings.skills_dir,
            disabled_skills=set(settings.agent.disabled_skills),
        )

    def build_system_prompt(
        self,
        *,
        workspace: Path | None = None,
        channel: str | None = None,
        session_key: str | None = None,
        session_summary: str | None = None,
        include_memory_recent_history: bool = True,
        extra_instructions: str | None = None,
    ) -> str:
        """Build the structured system prompt used for one provider turn."""
        root = (workspace or self.workspace).expanduser().resolve()

        parts = [self._identity_block(root, channel=channel, session_key=session_key)]

        bootstrap = self._load_bootstrap_files(root)
        if bootstrap:
            parts.append(bootstrap)

        parts.append(self._tool_contract_block())

        if self.settings.memory.enabled:
            memory_context = self.memory.get_memory_context()
            if memory_context:
                parts.append(f"# Memory\n\n{memory_context}")

            archived_history = (
                self.memory.render_recent_history_section(
                    session_key=session_key,
                    limit=_DEFAULT_RECENT_ARCHIVE_ENTRIES,
                    max_chars=_DEFAULT_RECENT_ARCHIVE_CHARS,
                )
                if include_memory_recent_history
                else ""
            )
            if archived_history:
                parts.append(archived_history)

        if session_summary:
            parts.append(f"[Archived Context Summary]\n\n{session_summary}")

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
        if skills_summary:
            parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

        if extra_instructions:
            parts.append(extra_instructions.strip())

        return "\n\n---\n\n".join(part for part in parts if part.strip())

    def build_messages(
        self,
        *,
        history: list[dict[str, Any]],
        current_message: str,
        current_role: str = "user",
        media: Sequence[str] | None = None,
        tools: ToolRegistry | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        session_key: str | None = None,
        workspace: Path | None = None,
        metadata: dict[str, Any] | None = None,
        extra_runtime_lines: Sequence[str] | None = None,
        extra_instructions: str | None = None,
        session_summary: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the full message list for one LLM call."""
        root = (workspace or self.workspace).expanduser().resolve()
        messages = [
            {
                "role": "system",
                "content": self.build_system_prompt(
                    workspace=root,
                    channel=channel,
                    session_key=session_key,
                    session_summary=session_summary,
                    include_memory_recent_history=True,
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
        user_content = self._build_user_content(current_message, media)
        merged_content = self._merge_message_content(user_content, runtime_ctx)

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
        media: Sequence[str] | None = None,
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
        session_summary = self._session_summary_text(session)
        messages = self.build_messages(
            history=history,
            current_message=current_message,
            current_role=current_role,
            media=media,
            tools=tool_registry,
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            workspace=root,
            metadata=metadata,
            extra_runtime_lines=extra_runtime_lines,
            extra_instructions=extra_instructions,
            session_summary=session_summary,
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
            current_media=list(media or []),
            current_role=current_role,
            model=self.settings.model_name,
            max_iterations=self.settings.max_iterations,
            max_tool_result_chars=self.settings.max_tool_result_chars,
            channel=channel,
            chat_id=chat_id,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _session_summary_text(session: Session) -> str | None:
        raw = session.metadata.get("_last_summary")
        if isinstance(raw, dict):
            text = raw.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
            return None
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

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
        runtime = f"{system} {machine}, Python {python_version}"
        return render_template(
            "agent/identity.md",
            identity=self.settings.identity.strip(),
            model_name=self.settings.model_name,
            runtime=runtime,
            workspace_path=str(workspace),
            sessions_path=str(self.settings.sessions_dir),
            memory_path=str(self.memory.memory_file),
            history_path=str(self.memory.history_file),
            skills_path=str(self.settings.skills_dir / "{skill-name}" / "SKILL.md"),
            platform_policy=render_template("agent/platform_policy.md", system=system),
            channel=(channel or "").strip().lower(),
            session_key=session_key or "",
        )

    @staticmethod
    def _tool_contract_block() -> str:
        return render_template("agent/tool_contract.md")

    def _load_bootstrap_files(self, workspace: Path | None = None) -> str:
        parts: list[str] = []
        roots = self._bootstrap_roots(workspace or self.workspace)
        for filename in self.BOOTSTRAP_FILES:
            for root in roots:
                path = root / filename
                if not path.exists() or not path.is_file():
                    continue
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"## {filename}\n\n{content}")
                    break
        return "\n\n".join(parts)

    def _bootstrap_roots(self, workspace: Path) -> list[Path]:
        roots = [Path(workspace).expanduser().resolve(strict=False)]
        fallback = self.bootstrap_dir.expanduser().resolve(strict=False)
        if fallback not in roots:
            roots.append(fallback)
        return roots

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
            lines.extend(goal_state_runtime_lines(self.sessions.get_or_create(session_key).metadata))
        if channel:
            lines.append(f"Channel: {channel}")
        if chat_id:
            lines.append(f"Chat ID: {chat_id}")

        meta = dict(metadata or {})
        message_id = meta.get("message_id")
        if isinstance(message_id, str) and message_id.strip():
            lines.append(f"Message ID: {message_id}")
        sender_id = meta.get("sender_id")
        if isinstance(sender_id, str) and sender_id.strip():
            lines.append(f"Sender ID: {sender_id}")
        lines.extend(self._metadata_runtime_lines(meta))

        if supplemental_lines:
            lines.extend(line for line in supplemental_lines if isinstance(line, str) and line.strip())

        return _RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines) + "\n" + _RUNTIME_CONTEXT_END

    @staticmethod
    def _metadata_runtime_lines(metadata: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        for key in ("_mcp_runtime_lines", "mcp_runtime_lines", "runtime_lines"):
            raw = metadata.get(key)
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
                lines.extend(str(item) for item in raw if str(item).strip())

        attachments = metadata.get("attachments") or metadata.get("media")
        if isinstance(attachments, Sequence) and not isinstance(attachments, (str, bytes, bytearray)):
            names = [Path(str(item)).name for item in attachments if str(item).strip()]
            if names:
                lines.append("Attachments: " + ", ".join(names))
        return lines

    @staticmethod
    def _detect_image_mime(path: Path, raw: bytes) -> str | None:
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if raw.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if raw.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            return "image/webp"
        guessed, _ = mimetypes.guess_type(path.name)
        if guessed and guessed.startswith("image/"):
            return guessed
        return None

    def _build_user_content(
        self,
        text: str,
        media: Sequence[str] | None,
    ) -> str | list[dict[str, Any]]:
        if not media:
            return text

        images: list[dict[str, Any]] = []
        for raw_path in media:
            path = Path(raw_path)
            if not path.is_file():
                continue
            payload = path.read_bytes()
            mime = self._detect_image_mime(path, payload)
            if not mime:
                continue
            images.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{base64.b64encode(payload).decode()}",
                    },
                    "_meta": {"path": str(path)},
                }
            )

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

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
