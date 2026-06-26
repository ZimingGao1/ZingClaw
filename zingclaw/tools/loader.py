"""Discovery and registration for built-in and plugin tools."""

from __future__ import annotations

import importlib
import pkgutil
from importlib.metadata import entry_points
from typing import Any

from loguru import logger

from zingclaw.tools.base import Tool
from zingclaw.tools.registry import ToolRegistry

_SKIP_MODULES = frozenset({
    "base",
    "context",
    "registry",
    "loader",
    "mcp",
    "__init__",
})


class ToolLoader:
    """Discover built-in tools and optional plugin tools, then register them."""

    def __init__(self, package: Any = None, *, test_classes: list[type[Tool]] | None = None):
        if package is None:
            import zingclaw.tools as _package

            package = _package
        self._package = package
        self._test_classes = test_classes
        self._discovered: list[type[Tool]] | None = None
        self._plugins: dict[str, type[Tool]] | None = None

    def discover(self) -> list[type[Tool]]:
        if self._test_classes is not None:
            return list(self._test_classes)
        if self._discovered is not None:
            return self._discovered

        seen: set[int] = set()
        results: list[type[Tool]] = []
        for _importer, module_name, _is_pkg in pkgutil.iter_modules(self._package.__path__):
            if module_name.startswith("_") or module_name in _SKIP_MODULES:
                continue
            try:
                module = importlib.import_module(f".{module_name}", self._package.__name__)
            except Exception:
                logger.exception("Failed to import tool module: {}", module_name)
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Tool)
                    and attr is not Tool
                    and not attr_name.startswith("_")
                    and not getattr(attr, "__abstractmethods__", None)
                    and getattr(attr, "_plugin_discoverable", True)
                    and id(attr) not in seen
                ):
                    seen.add(id(attr))
                    results.append(attr)

        results.sort(key=lambda cls: cls.__name__)
        self._discovered = results
        return results

    def _discover_plugins(self) -> dict[str, type[Tool]]:
        if self._plugins is not None:
            return self._plugins

        plugins: dict[str, type[Tool]] = {}
        try:
            raw_entry_points = entry_points()
            if hasattr(raw_entry_points, "select"):
                candidates = raw_entry_points.select(group="zingclaw.tools")
            else:
                candidates = raw_entry_points.get("zingclaw.tools", [])
        except Exception:
            self._plugins = plugins
            return plugins

        for entry_point_obj in candidates:
            try:
                loaded = entry_point_obj.load()
                if (
                    isinstance(loaded, type)
                    and issubclass(loaded, Tool)
                    and not getattr(loaded, "__abstractmethods__", None)
                    and getattr(loaded, "_plugin_discoverable", True)
                ):
                    plugins[entry_point_obj.name] = loaded
            except Exception:
                logger.exception("Failed to load tool plugin: {}", entry_point_obj.name)

        self._plugins = plugins
        return plugins

    def load(self, ctx: Any, registry: ToolRegistry, *, scope: str = "core") -> list[str]:
        registered: list[str] = []
        builtin_names: set[str] = set()
        sources = [(self.discover(), False), (self._discover_plugins().values(), True)]

        for source, is_plugin_source in sources:
            for tool_cls in source:
                class_label = tool_cls.__name__
                try:
                    if scope not in getattr(tool_cls, "_scopes", {"core"}):
                        continue
                    if not tool_cls.enabled(ctx):
                        continue
                    tool = tool_cls.create(ctx)
                    if registry.has(tool.name):
                        if is_plugin_source and tool.name in builtin_names:
                            logger.warning(
                                "Plugin {} skipped because it conflicts with built-in tool {}",
                                class_label,
                                tool.name,
                            )
                            continue
                        logger.warning(
                            "Tool name collision: {} from {} overwrites existing registration",
                            tool.name,
                            class_label,
                        )
                    registry.register(tool)
                    registered.append(tool.name)
                    if not is_plugin_source:
                        builtin_names.add(tool.name)
                except Exception:
                    logger.exception("Failed to register tool: {}", class_label)

        return registered
