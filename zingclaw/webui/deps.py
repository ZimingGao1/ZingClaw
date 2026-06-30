"""Shared aiohttp app keys for WebUI and gateway handlers."""

from __future__ import annotations

from aiohttp import web

from zingclaw.config import Settings
from zingclaw.gateway import GatewayRuntime

RUNTIME_KEY: web.AppKey[GatewayRuntime] = web.AppKey("gateway_runtime", GatewayRuntime)
SETTINGS_KEY: web.AppKey[Settings] = web.AppKey("settings", Settings)
TIMEOUT_KEY: web.AppKey[float] = web.AppKey("request_timeout_s", float)

__all__ = ["RUNTIME_KEY", "SETTINGS_KEY", "TIMEOUT_KEY"]
