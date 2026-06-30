"""Shared helpers for WebUI HTTP and WebSocket handlers."""

from __future__ import annotations

from dataclasses import asdict
import uuid
from typing import Any
from urllib.parse import unquote

from aiohttp import web

from zingclaw.gateway import GatewayRuntime

WEBSOCKET_SESSION_PREFIX = "websocket:"


def error_json(status: int, message: str) -> web.Response:
    return web.json_response(
        {"error": {"message": message, "type": "invalid_request_error", "code": status}},
        status=status,
    )


def websocket_session_key(chat_id: str) -> str:
    return f"{WEBSOCKET_SESSION_PREFIX}{chat_id}"


def websocket_frame(frame_type: str, **payload: Any) -> dict[str, Any]:
    return {"type": frame_type, **payload}


def decode_session_key(raw_value: str) -> str:
    value = unquote(raw_value).strip()
    if not value:
        raise ValueError("session key must not be empty")
    return value


async def json_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise ValueError("invalid JSON body") from exc
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")
    return body


def sorted_sessions(runtime: GatewayRuntime) -> list[dict[str, Any]]:
    sessions = runtime.loop.sessions.list_sessions()
    sessions.sort(key=lambda item: str(item.get("last_active_at") or item.get("updated_at") or ""), reverse=True)
    return sessions


def session_metadata_or_404(runtime: GatewayRuntime, key: str) -> dict[str, Any] | None:
    return runtime.loop.sessions.read_session_metadata(key)


def session_messages_or_404(runtime: GatewayRuntime, key: str) -> dict[str, Any] | None:
    return runtime.loop.sessions.read_session_file(key)


def mcp_status_dict(item: Any) -> dict[str, Any]:
    try:
        return asdict(item)
    except TypeError:
        return dict(item)


def generated_websocket_session_key() -> str:
    return websocket_session_key(uuid.uuid4().hex[:12])


__all__ = [
    "WEBSOCKET_SESSION_PREFIX",
    "decode_session_key",
    "error_json",
    "generated_websocket_session_key",
    "json_body",
    "mcp_status_dict",
    "session_messages_or_404",
    "session_metadata_or_404",
    "sorted_sessions",
    "websocket_frame",
    "websocket_session_key",
]
