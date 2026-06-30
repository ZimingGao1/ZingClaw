"""Prompt-template helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_template(template_path: str, /, **kwargs: object) -> str:
    """Render one bundled markdown template."""
    template = _environment().get_template(template_path)
    return template.render(**kwargs).strip()


def load_bundled_template(template_path: str) -> str | None:
    """Load raw template text from the bundled templates directory."""
    path = _TEMPLATES_DIR / template_path
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")
