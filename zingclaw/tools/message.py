"""Proactive message-delivery tool for bus-connected runtimes."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Any

from zingclaw.bus import OutboundMessage
from zingclaw.security import WorkspaceAccessPolicy
from zingclaw.tools.base import Tool, tool_parameters
from zingclaw.tools.context import RequestContext


class _MessageToolMixin:
    """Shared request context plumbing for proactive delivery."""

    def __init__(self) -> None:
        self._request_ctx: ContextVar[RequestContext | None] = ContextVar(
            f"{self.__class__.__name__}_request_ctx",
            default=None,
        )

    def set_context(self, ctx: RequestContext) -> Any:
        return self._request_ctx.set(ctx)

    def reset_context(self, token: Any) -> None:
        self._request_ctx.reset(token)

    def _current_request_context(self) -> RequestContext | None:
        return self._request_ctx.get()


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "Message content for proactive or cross-channel delivery. "
                    "Do not use this for the normal reply in the current chat."
                ),
                "minLength": 1,
                "maxLength": 20000,
            },
            "channel": {
                "type": ["string", "null"],
                "description": "Optional target channel. Defaults to the current runtime channel.",
            },
            "chat_id": {
                "type": ["string", "null"],
                "description": "Optional target chat or user id. Defaults to the current runtime chat.",
            },
            "media": {
                "type": ["array", "null"],
                "description": (
                    "Optional list of existing local file paths or http/https URLs to attach. "
                    "This is the only built-in tool that can carry media."
                ),
                "items": {"type": "string"},
                "maxItems": 20,
            },
        },
        "required": ["content"],
        "additionalProperties": False,
    }
)
class MessageTool(Tool, _MessageToolMixin):
    """Send a proactive message through the shared outbound bus."""

    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        bus = getattr(ctx, "bus", None)
        return cls(
            send_callback=None if bus is None else bus.publish_outbound,
            workspace=Path(ctx.workspace),
            restrict_to_workspace=bool(getattr(ctx, "restrict_to_workspace", True)),
        )

    def __init__(
        self,
        *,
        send_callback: Any = None,
        workspace: Path | None = None,
        restrict_to_workspace: bool = True,
    ) -> None:
        _MessageToolMixin.__init__(self)
        self._send_callback = send_callback
        self._workspace = Path(workspace or Path.cwd()).expanduser().resolve(strict=False)
        self._restrict_to_workspace = bool(restrict_to_workspace)
        self._access = WorkspaceAccessPolicy(
            workspace_root=self._workspace,
            restrict_to_workspace=self._restrict_to_workspace,
            allowed_root=self._workspace if self._restrict_to_workspace else None,
        )

    def set_send_callback(self, callback: Any) -> None:
        self._send_callback = callback

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return (
            "Proactively send a message to a user or channel, optionally with media attachments. "
            "Use this for reminders, cross-channel delivery, or explicit proactive sends. "
            "Do not use this for the normal reply in the current chat: answer naturally instead. "
            "This is the only built-in tool that can carry media."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        media: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        request_ctx = self._current_request_context()
        default_channel = None if request_ctx is None else request_ctx.channel
        default_chat_id = None if request_ctx is None else request_ctx.chat_id
        session_key = None if request_ctx is None else request_ctx.session_key

        target_channel = (channel or default_channel or "").strip()
        target_chat_id = (chat_id or default_chat_id or "").strip()
        if not target_channel or not target_chat_id:
            return "Error: No target channel/chat specified"
        if self._send_callback is None:
            return "Error: Message sending not configured"

        text = str(content or "").strip()
        if not text:
            return "Error: content must not be empty"

        current_message_id = None
        if request_ctx is not None:
            current_message_id = str(request_ctx.metadata.get("message_id") or "").strip() or None
        same_target = (
            request_ctx is not None
            and target_channel == (request_ctx.channel or "")
            and target_chat_id == (request_ctx.chat_id or "")
        )

        try:
            attachments = self._resolve_attachments(media or [])
        except Exception as exc:
            return f"Error: media path is not allowed: {exc}"

        outbound = OutboundMessage(
            channel=target_channel,
            chat_id=target_chat_id,
            session_key=(session_key or target_chat_id or "default"),
            text=text,
            reply_to=current_message_id if same_target else None,
            final=True,
            attachments=attachments,
            metadata={
                "_tool_message": True,
                "source": "message_tool",
            },
        )
        await self._send_callback(outbound)

        attachment_note = f" with {len(attachments)} attachment(s)" if attachments else ""
        return f"Message sent to {target_channel}:{target_chat_id}{attachment_note}"

    def _resolve_attachments(self, media: list[str]) -> list[dict[str, str]]:
        attachments: list[dict[str, str]] = []
        for item in media:
            value = str(item or "").strip()
            if not value:
                continue
            if value.startswith(("http://", "https://")):
                attachments.append({"type": "url", "url": value})
                continue
            path = self._access.resolve_path(value)
            attachments.append({"type": "file", "path": str(path)})
        return attachments
