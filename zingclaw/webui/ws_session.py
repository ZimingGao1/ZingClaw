"""Reusable WebSocket session state and frame routing for the browser client."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

from aiohttp import WSMessage, WSMsgType, web

from zingclaw.gateway import GatewayEvent, GatewayRuntime, GatewaySubscription
from zingclaw.webui.common import (
    WEBSOCKET_SESSION_PREFIX,
    decode_session_key,
    websocket_frame,
    websocket_session_key,
)
from zingclaw.webui.session_service import SessionNotFoundError, SessionService


@dataclass(slots=True)
class WebSocketSessionState:
    """Track one browser WebSocket's active session selection and subscription."""

    runtime: GatewayRuntime
    service: SessionService
    model_name: str
    session_key: str
    chat_id: str
    subscription: GatewaySubscription

    @classmethod
    def open(
        cls,
        runtime: GatewayRuntime,
        *,
        model_name: str,
        requested_chat_id: str = "",
        requested_session_key: str = "",
    ) -> WebSocketSessionState:
        chat_id = requested_chat_id.strip() or uuid.uuid4().hex[:12]
        session_key = requested_session_key.strip() or websocket_session_key(chat_id)
        return cls(
            runtime=runtime,
            service=SessionService(runtime),
            model_name=model_name,
            session_key=session_key,
            chat_id=chat_id,
            subscription=runtime.subscribe(
                session_key=session_key,
                channel="websocket",
                chat_id=chat_id,
            ),
        )

    def close(self) -> None:
        self.runtime.unsubscribe(self.subscription)

    def ready_frame(self, frame_type: str = "ready") -> dict[str, Any]:
        return websocket_frame(
            frame_type,
            chat_id=self.chat_id,
            session_key=self.session_key,
            model=self.model_name,
        )

    def event_frame(self, event: GatewayEvent) -> dict[str, Any]:
        return websocket_frame("event", event=event.to_dict())

    async def switch_session(self, next_session_key: str, next_chat_id: str | None = None) -> None:
        self.runtime.unsubscribe(self.subscription)
        self.session_key = next_session_key
        self.chat_id = (
            next_chat_id
            or next_session_key.removeprefix(WEBSOCKET_SESSION_PREFIX)
            or uuid.uuid4().hex[:12]
        ).strip()
        self.subscription = self.runtime.subscribe(
            session_key=self.session_key,
            channel="websocket",
            chat_id=self.chat_id,
        )

    async def accept_user_message(self, body: dict[str, Any]) -> dict[str, Any]:
        message_id = await self.service.submit_user_message(
            text=body.get("text"),
            session_key=self.session_key,
            chat_id=self.chat_id,
            metadata=body.get("metadata"),
            stream=body.get("stream", True),
            message_id=str(body.get("message_id") or uuid.uuid4().hex),
        )
        return websocket_frame(
            "accepted",
            message_id=message_id,
            chat_id=self.chat_id,
            session_key=self.session_key,
        )


async def wait_for_socket_or_event(
    ws: web.WebSocketResponse,
    subscription: GatewaySubscription,
    *,
    timeout_s: float,
) -> tuple[str, GatewayEvent | WSMessage] | None:
    async def _recv_client_message() -> WSMessage:
        return await ws.receive()

    socket_task = asyncio.create_task(_recv_client_message())
    event_task = asyncio.create_task(subscription.next())
    done, pending = await asyncio.wait(
        {socket_task, event_task},
        timeout=timeout_s,
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    if not done:
        return None
    if event_task in done:
        return "event", event_task.result()
    return "socket", socket_task.result()


def parse_text_frame(msg: WSMessage) -> dict[str, Any] | None:
    if msg.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING, WSMsgType.ERROR}:
        return None
    if msg.type != WSMsgType.TEXT:
        return websocket_frame("error", message="only text JSON frames are supported")
    try:
        body = json.loads(msg.data)
    except json.JSONDecodeError:
        return websocket_frame("error", message="invalid JSON frame")
    if not isinstance(body, dict):
        return websocket_frame("error", message="frame body must be an object")
    return body


def _session_key_from_body(
    body: dict[str, Any],
    *,
    default_key: str,
    required_message: str | None = None,
) -> str:
    raw_key = str(body.get("session_key") or ("" if required_message else default_key)).strip()
    if required_message and not raw_key:
        raise ValueError(required_message)
    return decode_session_key(raw_key)


async def route_websocket_frame(
    state: WebSocketSessionState,
    body: dict[str, Any],
) -> list[dict[str, Any]]:
    frame_type = str(body.get("type") or "").strip().lower()

    try:
        if frame_type == "ping":
            return [websocket_frame("pong")]
        if frame_type == "list_sessions":
            return [websocket_frame("session_list", sessions=state.service.list_sessions())]
        if frame_type == "get_session":
            target_key = _session_key_from_body(body, default_key=state.session_key)
            detail = state.service.get_detail(target_key)
            return [websocket_frame("session_detail", **detail)]
        if frame_type == "create_session":
            session = state.service.create_session(
                key=str(body.get("key") or "").strip(),
                title=str(body.get("title") or "").strip(),
            )
            return [websocket_frame("session_created", session=session)]
        if frame_type == "switch_session":
            target_key = _session_key_from_body(
                body,
                default_key=state.session_key,
                required_message="switch_session.session_key must not be empty",
            )
            session = state.service.ensure_session(target_key)
            await state.switch_session(target_key)
            return [
                websocket_frame("session_switched", session=session),
                state.ready_frame("ready"),
            ]
        if frame_type == "rename_session":
            target_key = _session_key_from_body(body, default_key=state.session_key)
            session = state.service.rename_session(target_key, str(body.get("title") or "").strip())
            return [websocket_frame("session_renamed", session=session)]
        if frame_type == "clear_session":
            target_key = _session_key_from_body(body, default_key=state.session_key)
            session = state.service.clear_session(target_key)
            return [websocket_frame("session_cleared", session=session)]
        if frame_type == "delete_session":
            target_key = _session_key_from_body(body, default_key=state.session_key)
            state.service.delete_session(target_key)
            return [websocket_frame("session_deleted", session_key=target_key)]
        if frame_type == "user_message":
            return [await state.accept_user_message(body)]
    except SessionNotFoundError:
        return [websocket_frame("error", message="session not found")]
    except ValueError as exc:
        message = str(exc)
        if frame_type == "rename_session" and message == "title must not be empty":
            message = "rename_session.title must not be empty"
        return [websocket_frame("error", message=message)]

    return [websocket_frame("error", message=f"unsupported websocket frame type: {frame_type or '(empty)'}")]


__all__ = [
    "WebSocketSessionState",
    "parse_text_frame",
    "route_websocket_frame",
    "wait_for_socket_or_event",
]
