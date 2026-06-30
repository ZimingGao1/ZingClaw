"""Static WebUI HTTP handlers."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from zingclaw.webui import static_dir as webui_static_dir

_WEBUI_STATIC_DIR = webui_static_dir()
_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def webui_asset_path(relative_path: str) -> Path:
    candidate = (_WEBUI_STATIC_DIR / relative_path).resolve()
    try:
        candidate.relative_to(_WEBUI_STATIC_DIR)
    except ValueError as exc:
        raise FileNotFoundError(relative_path) from exc
    if not candidate.is_file():
        raise FileNotFoundError(relative_path)
    return candidate


async def handle_webui_index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(webui_asset_path("index.html"), headers=dict(_NO_CACHE_HEADERS))


async def handle_webui_asset(request: web.Request) -> web.StreamResponse:
    raw_path = str(request.match_info["asset_path"] or "").strip()
    if not raw_path:
        return web.FileResponse(webui_asset_path("index.html"), headers=dict(_NO_CACHE_HEADERS))
    try:
        return web.FileResponse(webui_asset_path(raw_path), headers=dict(_NO_CACHE_HEADERS))
    except FileNotFoundError:
        raise web.HTTPNotFound(text="asset not found")


def register_webui_http_routes(app: web.Application) -> None:
    app.router.add_get("/", handle_webui_index)
    app.router.add_get("/webui", handle_webui_index)
    app.router.add_get("/webui/{asset_path:.*}", handle_webui_asset)


__all__ = [
    "handle_webui_asset",
    "handle_webui_index",
    "register_webui_http_routes",
    "webui_asset_path",
]
