"""HTTP MCP-management handlers for the WebUI."""

from __future__ import annotations

from aiohttp import web

from zingclaw.webui.common import mcp_status_dict
from zingclaw.webui.deps import RUNTIME_KEY


async def handle_mcp_status(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    statuses = await runtime.loop.get_mcp_status()
    return web.json_response({"servers": [mcp_status_dict(item) for item in statuses]})


async def handle_mcp_reload(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    result = await runtime.loop.reload_mcp()
    statuses = await runtime.loop.get_mcp_status(connect_missing=False)
    return web.json_response(
        {
            "ok": bool(result.get("ok")),
            "message": str(result.get("message") or "MCP reload completed."),
            "requires_restart": bool(result.get("requires_restart", False)),
            "result": result,
            "servers": [mcp_status_dict(item) for item in statuses],
        }
    )


async def handle_mcp_reconnect(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME_KEY]
    try:
        body = await request.json()
    except Exception:
        body = {}
    server_name = body.get("server") if isinstance(body, dict) else None
    result = await runtime.loop.reconnect_mcp(str(server_name).strip() or None)
    statuses = await runtime.loop.get_mcp_status(connect_missing=False)
    return web.json_response(
        {
            "ok": bool(result.get("ok")),
            "message": str(result.get("message") or "MCP reconnect completed."),
            "result": result,
            "servers": [mcp_status_dict(item) for item in statuses],
        }
    )


def register_mcp_routes(app: web.Application) -> None:
    app.router.add_get("/api/mcp/status", handle_mcp_status)
    app.router.add_post("/api/mcp/reload", handle_mcp_reload)
    app.router.add_post("/api/mcp/reconnect", handle_mcp_reconnect)


__all__ = [
    "handle_mcp_reconnect",
    "handle_mcp_reload",
    "handle_mcp_status",
    "register_mcp_routes",
]
