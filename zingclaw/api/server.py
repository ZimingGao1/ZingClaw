"""Minimal API and SSE gateway for ZingClaw."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from aiohttp import web

from zingclaw.config import Settings
from zingclaw.gateway import GatewayEvent, GatewayRuntime
from zingclaw.webui.common import error_json
from zingclaw.webui.deps import RUNTIME_KEY, SETTINGS_KEY, TIMEOUT_KEY
from zingclaw.webui.http import register_webui_http_routes
from zingclaw.webui.mcp_api import register_mcp_routes
from zingclaw.webui.session_api import register_session_routes
from zingclaw.webui.ws_api import register_ws_routes

API_SESSION_KEY = "api:default"
_SSE_DONE = b"data: [DONE]\n\n"


def _parse_user_text(body: dict[str, Any]) -> str:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        raise ValueError("last message must be a user message")
    content = last.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    raise ValueError("unsupported message content")


def _chat_completion_response(
    *,
    content: str,
    model: str,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    prompt = (usage or {}).get("prompt_tokens", 0)
    completion = (usage or {}).get("completion_tokens", 0)
    total = (usage or {}).get("total_tokens", 0) or prompt + completion
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        },
    }


def _sse_chunk(delta: str, *, model: str, chunk_id: str, finish_reason: str | None = None) -> bytes:
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": delta} if delta else {},
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


async def _wait_for_final_outbound(
    subscription,
    *,
    message_id: str,
    timeout_s: float,
) -> GatewayEvent:
    while True:
        event = await asyncio.wait_for(subscription.next(), timeout=timeout_s)
        if event.kind != "outbound":
            continue
        payload = event.payload
        if payload.get("reply_to") != message_id:
            continue
        if payload.get("final") is True:
            return event


async def handle_models(request: web.Request) -> web.Response:
    settings = request.app[SETTINGS_KEY]
    model = settings.model_name
    return web.json_response(
        {
            "object": "list",
            "data": [
                {
                    "id": model,
                    "object": "model",
                    "owned_by": "zingclaw",
                }
            ],
        }
    )


async def handle_runtime_events(request: web.Request) -> web.StreamResponse:
    runtime = request.app[RUNTIME_KEY]
    session_key = request.query.get("session_key")
    channel = request.query.get("channel")
    chat_id = request.query.get("chat_id")
    subscription = runtime.subscribe(session_key=session_key, channel=channel, chat_id=chat_id)

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await response.prepare(request)
    try:
        while True:
            event = await subscription.next()
            blob = json.dumps(event.to_dict(), ensure_ascii=False)
            await response.write(f"data: {blob}\n\n".encode("utf-8"))
    except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
        raise
    finally:
        runtime.unsubscribe(subscription)


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    runtime = request.app[RUNTIME_KEY]
    settings = request.app[SETTINGS_KEY]
    timeout_s = request.app[TIMEOUT_KEY]
    try:
        body = await request.json()
    except Exception:
        return error_json(400, "invalid JSON body")

    try:
        text = _parse_user_text(body)
    except ValueError as exc:
        return error_json(400, str(exc))

    session_key = str(body.get("session_id") or API_SESSION_KEY)
    stream = bool(body.get("stream", False))
    model = str(body.get("model") or settings.model_name)
    request_id = uuid.uuid4().hex
    subscription = runtime.subscribe(session_key=session_key, channel="api")

    try:
        message_id = await runtime.submit_user_message(
            text=text,
            session_key=session_key,
            channel="api",
            chat_id=session_key,
            metadata={"_request_id": request_id},
            wants_stream=stream,
        )

        if not stream:
            event = await _wait_for_final_outbound(subscription, message_id=message_id, timeout_s=timeout_s)
            payload = event.payload
            return web.json_response(
                _chat_completion_response(
                    content=str(payload.get("text") or ""),
                    model=model,
                    usage=payload.get("metadata", {}).get("usage"),
                )
            )

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        while True:
            event = await asyncio.wait_for(subscription.next(), timeout=timeout_s)
            if event.kind != "outbound":
                continue
            payload = event.payload
            if payload.get("reply_to") != message_id:
                continue
            metadata = payload.get("metadata", {})
            if metadata.get("_stream_delta"):
                await response.write(
                    _sse_chunk(str(payload.get("text") or ""), model=model, chunk_id=chunk_id)
                )
                continue
            if payload.get("final") is True:
                await response.write(_sse_chunk("", model=model, chunk_id=chunk_id, finish_reason="stop"))
                await response.write(_SSE_DONE)
                return response
    except asyncio.TimeoutError:
        return error_json(504, "request timed out")
    finally:
        runtime.unsubscribe(subscription)


def create_app(runtime: GatewayRuntime, settings: Settings, *, request_timeout_s: float = 120.0) -> web.Application:
    app = web.Application()
    app[RUNTIME_KEY] = runtime
    app[SETTINGS_KEY] = settings
    app[TIMEOUT_KEY] = float(request_timeout_s)

    async def _health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app.router.add_get("/health", _health)
    register_webui_http_routes(app)
    app.router.add_get("/v1/models", handle_models)
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    app.router.add_get("/runtime/events", handle_runtime_events)
    register_ws_routes(app)
    register_session_routes(app)
    register_mcp_routes(app)

    async def _start_runtime(_app: web.Application) -> None:
        await runtime.start()

    async def _stop_runtime(_app: web.Application) -> None:
        await runtime.stop()

    app.on_startup.append(_start_runtime)
    app.on_cleanup.append(_stop_runtime)
    return app
