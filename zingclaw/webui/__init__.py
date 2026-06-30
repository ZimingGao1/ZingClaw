"""Static WebUI assets for the minimal ZingClaw browser client."""

from pathlib import Path


def static_dir() -> Path:
    """Return the packaged static asset directory."""

    return Path(__file__).resolve().parent / "static"


__all__ = ["static_dir"]
