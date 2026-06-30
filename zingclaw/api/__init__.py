"""HTTP API entrypoints for ZingClaw."""

from __future__ import annotations

from typing import Any

__all__ = ["create_app"]


def create_app(*args: Any, **kwargs: Any) -> Any:
    """Lazily import the aiohttp server factory."""

    from zingclaw.api.server import create_app as _create_app

    return _create_app(*args, **kwargs)
