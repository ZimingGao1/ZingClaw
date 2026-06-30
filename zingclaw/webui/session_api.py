"""HTTP session-management handlers for the WebUI."""

from __future__ import annotations

from aiohttp import web

from zingclaw.webui.common import (
    decode_session_key,
    error_json,
    json_body,
)
from zingclaw.webui.deps import RUNTIME_KEY
from zingclaw.webui.session_service import SessionNotFoundError, SessionService


async def handle_sessions_list(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    service = SessionService(runtime)
    return web.json_response({"sessions": service.list_sessions()})


async def handle_session_create(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    service = SessionService(runtime)
    try:
        body = await json_body(request)
    except ValueError as exc:
        return error_json(400, str(exc))

    payload = service.create_session(
        key=str(body.get("key") or "").strip(),
        title=str(body.get("title") or "").strip(),
    )
    return web.json_response(payload, status=201)


async def handle_session_detail(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    service = SessionService(runtime)
    try:
        key = decode_session_key(request.match_info["session_key"])
    except ValueError as exc:
        return error_json(400, str(exc))

    try:
        payload = service.get_metadata(key)
    except SessionNotFoundError:
        return error_json(404, "session not found")
    return web.json_response(payload)


async def handle_session_messages(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    service = SessionService(runtime)
    try:
        key = decode_session_key(request.match_info["session_key"])
    except ValueError as exc:
        return error_json(400, str(exc))

    try:
        payload = service.get_messages(key)
    except SessionNotFoundError:
        return error_json(404, "session not found")
    return web.json_response(payload)


async def handle_session_rename(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    service = SessionService(runtime)
    try:
        key = decode_session_key(request.match_info["session_key"])
        body = await json_body(request)
    except ValueError as exc:
        return error_json(400, str(exc))

    try:
        payload = service.rename_session(key, str(body.get("title") or "").strip())
    except ValueError as exc:
        return error_json(400, str(exc))
    return web.json_response(payload)


async def handle_session_clear(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    service = SessionService(runtime)
    try:
        key = decode_session_key(request.match_info["session_key"])
    except ValueError as exc:
        return error_json(400, str(exc))

    try:
        payload = service.clear_session(key)
    except SessionNotFoundError:
        return error_json(404, "session not found")
    return web.json_response(payload)


async def handle_session_delete(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    service = SessionService(runtime)
    try:
        key = decode_session_key(request.match_info["session_key"])
    except ValueError as exc:
        return error_json(400, str(exc))

    try:
        service.delete_session(key)
    except SessionNotFoundError:
        return error_json(404, "session not found")
    return web.json_response({"ok": True, "deleted": key})


def register_session_routes(app: web.Application) -> None:
    app.router.add_get("/api/sessions", handle_sessions_list)
    app.router.add_post("/api/sessions", handle_session_create)
    app.router.add_get("/api/sessions/{session_key}", handle_session_detail)
    app.router.add_get("/api/sessions/{session_key}/messages", handle_session_messages)
    app.router.add_post("/api/sessions/{session_key}/rename", handle_session_rename)
    app.router.add_post("/api/sessions/{session_key}/clear", handle_session_clear)
    app.router.add_delete("/api/sessions/{session_key}", handle_session_delete)


__all__ = [
    "handle_session_clear",
    "handle_session_create",
    "handle_session_delete",
    "handle_session_detail",
    "handle_session_messages",
    "handle_session_rename",
    "handle_sessions_list",
    "register_session_routes",
]
