"""WebSocket session API for the browser client."""

from __future__ import annotations

from aiohttp import web

from zingclaw.webui.common import websocket_frame
from zingclaw.webui.deps import RUNTIME_KEY, SETTINGS_KEY, TIMEOUT_KEY
from zingclaw.webui.ws_session import (
    WebSocketSessionState,
    parse_text_frame,
    route_websocket_frame,
    wait_for_socket_or_event,
)


async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    runtime = request.app[RUNTIME_KEY]
    settings = request.app[SETTINGS_KEY]
    timeout_s = request.app[TIMEOUT_KEY]

    ws = web.WebSocketResponse(heartbeat=30.0)
    await ws.prepare(request)

    state = WebSocketSessionState.open(
        runtime,
        model_name=settings.model_name,
        requested_chat_id=str(request.query.get("chat_id") or ""),
        requested_session_key=str(request.query.get("session_key") or ""),
    )
    await ws.send_json(state.ready_frame())

    try:
        while True:
            item = await wait_for_socket_or_event(ws, state.subscription, timeout_s=timeout_s)
            if item is None:
                await ws.send_json(websocket_frame("error", message="websocket session timed out"))
                break

            kind, payload = item
            if kind == "event":
                await ws.send_json(state.event_frame(payload))
                continue

            parsed = parse_text_frame(payload)
            if parsed is None:
                break
            if parsed.get("type") == "error":
                await ws.send_json(parsed)
                continue

            for frame in await route_websocket_frame(state, parsed):
                await ws.send_json(frame)
    finally:
        state.close()
        await ws.close()
    return ws


def register_ws_routes(app: web.Application) -> None:
    app.router.add_get("/ws", handle_websocket)


__all__ = ["handle_websocket", "register_ws_routes"]
