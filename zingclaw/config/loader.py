"""Config document loading and saving helpers."""

from __future__ import annotations

import json
from pathlib import Path

from zingclaw.config.schema import ZingClawConfig
from zingclaw.config.utils import resolve_config_path, resolve_env_refs


def load_config(
    config_path: str | Path | None = None,
    *,
    home_dir: str | Path | None = None,
    env_file: str | Path | tuple[str | Path, ...] | None = ".env",
) -> ZingClawConfig:
    """Load a structured ZingClaw config document from disk."""
    path = resolve_config_path(config_path=config_path, home_dir=home_dir, env_file=env_file)
    if not path.exists():
        return ZingClawConfig(home_dir=path.parent)

    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)

    data = resolve_env_refs(raw, env_file=env_file)
    if "homeDir" not in data and "home_dir" not in data:
        data["home_dir"] = str(path.parent)
    return ZingClawConfig.model_validate(data)


def save_config(
    config: ZingClawConfig,
    config_path: str | Path | None = None,
    *,
    env_file: str | Path | tuple[str | Path, ...] | None = ".env",
) -> Path:
    """Persist a structured config document to disk."""
    path = resolve_config_path(config_path=config_path, home_dir=config.home_dir, env_file=env_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config.to_document(), handle, indent=2, ensure_ascii=False)
    return path
