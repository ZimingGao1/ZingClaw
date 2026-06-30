"""Shared helpers for ZingClaw configuration models and loaders."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_FILENAME = "config.json"
_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def default_home_dir() -> Path:
    """Return the default runtime home directory."""
    return Path.cwd()


def resolve_runtime_dir(value: str | Path | None, home_dir: Path, leaf_name: str) -> Path:
    """Resolve one runtime directory relative to the configured home."""
    candidate = Path(value).expanduser() if value is not None else home_dir / leaf_name
    if not candidate.is_absolute():
        candidate = home_dir / candidate
    return candidate.resolve()


def read_dotenv_values(env_file: str | Path | tuple[str | Path, ...] | None = ".env") -> dict[str, str]:
    """Read simple KEY=value pairs from one or more dotenv files."""
    if env_file is None:
        return {}

    files: tuple[str | Path, ...]
    if isinstance(env_file, tuple):
        files = env_file
    elif isinstance(env_file, list):
        files = tuple(env_file)
    else:
        files = (env_file,)

    values: dict[str, str] = {}
    for file_name in files:
        path = Path(file_name)
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
                value = value[1:-1]
            values[key] = value
    return values


def resolve_config_path(
    *,
    config_path: str | Path | None = None,
    home_dir: str | Path | None = None,
    env_file: str | Path | tuple[str | Path, ...] | None = ".env",
) -> Path:
    """Resolve the active config.json path."""
    if config_path is not None:
        return Path(config_path).expanduser().resolve()

    dotenv_values = read_dotenv_values(env_file)
    explicit_path = (
        os.environ.get("ZINGCLAW_CONFIG_PATH")
        or os.environ.get("ZINGCLAW_CONFIG")
        or dotenv_values.get("ZINGCLAW_CONFIG_PATH")
        or dotenv_values.get("ZINGCLAW_CONFIG")
    )
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()

    resolved_home = home_dir
    if resolved_home is None:
        resolved_home = (
            os.environ.get("ZINGCLAW_HOME")
            or os.environ.get("ZINGCLAW_HOME_DIR")
            or dotenv_values.get("ZINGCLAW_HOME")
            or dotenv_values.get("ZINGCLAW_HOME_DIR")
        )

    home_path = Path(resolved_home).expanduser() if resolved_home is not None else default_home_dir()
    if not home_path.is_absolute():
        home_path = default_home_dir() / home_path
    return (home_path.resolve() / DEFAULT_CONFIG_FILENAME).resolve()


def resolve_env_refs(
    obj: Any,
    *,
    env_file: str | Path | tuple[str | Path, ...] | None = ".env",
) -> Any:
    """Resolve ${VAR} references using dotenv values plus the current environment."""
    merged_env = read_dotenv_values(env_file)
    merged_env.update(os.environ)

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = merged_env.get(name)
        if value is None:
            raise ValueError(f"Environment variable '{name}' referenced in config is not set")
        return value

    if isinstance(obj, str):
        return _ENV_REF_PATTERN.sub(_replace, obj)
    if isinstance(obj, list):
        return [resolve_env_refs(item, env_file=env_file) for item in obj]
    if isinstance(obj, dict):
        return {key: resolve_env_refs(value, env_file=env_file) for key, value in obj.items()}
    return obj
