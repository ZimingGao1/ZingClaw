"""MCP runtime plus wrappers that expose remote capabilities as local tools."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import shutil
import urllib.parse
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

import httpx
from loguru import logger

from zingclaw.config import MCPServerConfig, Settings
from zingclaw.tools.base import Tool
from zingclaw.tools.registry import ToolRegistry

_SANITIZE_RE = re.compile(r"_+")
_WINDOWS_SHELL_LAUNCHERS = frozenset({"npx", "npm", "pnpm", "yarn", "bunx"})
MCP_PRESETS_KEY = "mcp_presets"
INBOUND_META_RUNTIME_CONTROL = "_runtime_control"
RUNTIME_CONTROL_ACK = "_runtime_control_ack"
RUNTIME_CONTROL_MCP_RELOAD = "mcp_reload"


def sanitize_mcp_name(name: str) -> str:
    """Sanitize an MCP-derived tool name for model-provider compatibility."""
    return _SANITIZE_RE.sub("_", re.sub(r"[^a-zA-Z0-9_-]", "_", name)).strip("_")


def _tool_prefix(server_name: str) -> str:
    return _SANITIZE_RE.sub("_", re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp_{server_name}_"))


def session_extra(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return session metadata that should persist MCP preset attachments."""
    mcp_presets = metadata.get(MCP_PRESETS_KEY) if isinstance(metadata, Mapping) else None
    return {MCP_PRESETS_KEY: mcp_presets} if isinstance(mcp_presets, list) and mcp_presets else {}


def preset_runtime_lines(
    metadata: Mapping[str, Any] | None,
    *,
    configured_server_names: set[str] | None = None,
    connected_server_names: set[str] | None = None,
    skip: bool = False,
) -> list[str]:
    """Return model-visible MCP preset attachment annotations."""
    if skip:
        return []

    structured = metadata.get(MCP_PRESETS_KEY) if isinstance(metadata, Mapping) else None
    if not isinstance(structured, list):
        return []

    lines: list[str] = []
    for item in structured[:8]:
        if not isinstance(item, Mapping):
            continue
        raw_name = str(item.get("name") or "").strip().lower()
        if not raw_name:
            continue
        display = str(item.get("display_name") or raw_name).strip() or raw_name
        transport = str(item.get("transport") or "mcp").strip() or "mcp"
        prefix = _tool_prefix(raw_name)
        if configured_server_names is not None and raw_name not in configured_server_names:
            lines.append(
                "MCP Preset Attachment: "
                f"@{raw_name} ({display}; transport={transport}) is attached to this session, "
                "but the current runtime has not loaded a matching MCP server config yet. "
                f"Tools with prefix `{prefix}` may be missing."
            )
            continue
        if connected_server_names is not None and raw_name not in connected_server_names:
            lines.append(
                "MCP Preset Attachment: "
                f"@{raw_name} ({display}; transport={transport}) is configured, "
                "but its MCP connection is not currently live. "
                f"Tools with prefix `{prefix}` may be unavailable."
            )
            continue
        lines.append(
            "MCP Preset Attachment: "
            f"@{raw_name} ({display}; transport={transport}; tool_prefix={prefix}). "
            f"Prefer available tools whose names start with `{prefix}` for this request; "
            "do not substitute shell commands for this MCP integration unless the user asks."
        )
    return lines


async def request_mcp_reload(bus: Any, *, timeout: float = 15.0) -> dict[str, Any]:
    """Ask the running AgentLoop to reload MCP config through the inbound bus."""
    from zingclaw.bus import InboundMessage

    loop = asyncio.get_running_loop()
    ack: asyncio.Future[dict[str, Any]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            chat_id="runtime",
            session_key="runtime:mcp",
            sender_id="cli",
            text=RUNTIME_CONTROL_MCP_RELOAD,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_MCP_RELOAD,
                RUNTIME_CONTROL_ACK: ack,
            },
        )
    )
    try:
        result = await asyncio.wait_for(ack, timeout=timeout)
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "message": "MCP hot reload timed out. Restart ZingClaw if the runtime is stuck.",
            "requires_restart": True,
        }
    if isinstance(result, dict):
        return result
    return {
        "ok": False,
        "message": "MCP hot reload returned an unexpected response.",
        "requires_restart": True,
    }


def _windows_command_basename(command: str) -> str:
    return command.replace("\\", "/").rsplit("/", maxsplit=1)[-1].lower()


def _normalize_windows_stdio_command(
    command: str,
    args: list[str] | None,
    env: dict[str, str] | None,
) -> tuple[str, list[str], dict[str, str] | None]:
    """Wrap Windows shell launchers so stdio MCP servers start reliably."""
    normalized_args = list(args or [])
    if os.name != "nt":
        return command, normalized_args, env

    basename = _windows_command_basename(command)
    if basename in {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return command, normalized_args, env

    if basename.endswith((".exe", ".com")):
        return command, normalized_args, env

    resolved = shutil.which(command, path=(env or {}).get("PATH")) or command
    resolved_basename = _windows_command_basename(resolved)
    should_wrap = (
        basename in _WINDOWS_SHELL_LAUNCHERS
        or basename.endswith((".cmd", ".bat"))
        or resolved_basename.endswith((".cmd", ".bat"))
    )
    if not should_wrap:
        return command, normalized_args, env

    comspec = (env or {}).get("COMSPEC") or os.environ.get("COMSPEC") or "cmd.exe"
    return comspec, ["/d", "/c", command, *normalized_args], env


def _extract_nullable_branch(options: Any) -> tuple[dict[str, Any], bool] | None:
    if not isinstance(options, list):
        return None

    non_null: list[dict[str, Any]] = []
    saw_null = False
    for option in options:
        if not isinstance(option, dict):
            return None
        if option.get("type") == "null":
            saw_null = True
            continue
        non_null.append(option)

    if saw_null and len(non_null) == 1:
        return non_null[0], True
    return None


def _normalize_schema_for_openai(schema: Any) -> dict[str, Any]:
    """Normalize MCP JSON Schema to the subset our tool validator expects."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": []}

    normalized = {
        key: value
        for key, value in dict(schema).items()
        if key not in {"$schema", "additionalProperties"}
    }
    raw_type = normalized.get("type")
    if isinstance(raw_type, list):
        non_null = [item for item in raw_type if item != "null"]
        if "null" in raw_type and len(non_null) == 1:
            normalized["type"] = non_null[0]
            normalized["nullable"] = True

    for key in ("oneOf", "anyOf"):
        nullable_branch = _extract_nullable_branch(normalized.get(key))
        if nullable_branch is None:
            continue
        branch, _nullable = nullable_branch
        merged = {k: v for k, v in normalized.items() if k != key}
        merged.update(branch)
        merged["nullable"] = True
        normalized = merged
        break

    required = normalized.get("required")
    if isinstance(required, str):
        normalized["required"] = [required]
    elif isinstance(required, tuple):
        normalized["required"] = list(required)
    elif required is not None and not isinstance(required, list):
        normalized["required"] = [str(required)]

    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["properties"] = {
            name: _normalize_schema_for_openai(prop) if isinstance(prop, dict) else prop
            for name, prop in properties.items()
        }

    items = normalized.get("items")
    if isinstance(items, dict):
        normalized["items"] = _normalize_schema_for_openai(items)

    for key in ("allOf",):
        value = normalized.get(key)
        if isinstance(value, list):
            normalized[key] = [
                _normalize_schema_for_openai(item) if isinstance(item, dict) else item
                for item in value
            ]

    if normalized.get("type") == "object":
        normalized.setdefault("properties", {})
        normalized.setdefault("required", [])
    return normalized


def _render_resource_contents(resource: Any) -> str:
    from mcp import types

    if isinstance(resource, types.TextResourceContents):
        return resource.text
    if isinstance(resource, types.BlobResourceContents):
        return f"[Binary resource: {len(resource.blob)} bytes]"
    return str(resource)


def _render_content_block(block: Any) -> str:
    from mcp import types

    if isinstance(block, types.TextContent):
        return block.text
    if isinstance(block, types.EmbeddedResource):
        return _render_resource_contents(block.resource)
    return str(block)


@dataclass(slots=True)
class MCPServerStatus:
    """Human-friendly runtime state for one configured MCP server."""

    name: str
    transport: str
    connected: bool
    tool_prefix: str
    target: str
    tool_count: int = 0
    resource_count: int = 0
    prompt_count: int = 0
    capability_count: int = 0
    capabilities: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    resource_names: list[str] = field(default_factory=list)
    prompt_names: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class _ConnectedServer:
    config: MCPServerConfig
    stack: AsyncExitStack
    session: Any
    capability_names: list[str]
    tool_count: int = 0
    resource_count: int = 0
    prompt_count: int = 0
    tool_names: list[str] = field(default_factory=list)
    resource_names: list[str] = field(default_factory=list)
    prompt_names: list[str] = field(default_factory=list)


async def _probe_http_url(url: str, timeout: float = 3.0) -> bool:
    """Quick TCP probe so dead HTTP MCP targets fail fast and predictably."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        with suppress(OSError, asyncio.TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), timeout=0.2)
        del reader
        return True
    except (OSError, asyncio.TimeoutError):
        return False


class MCPToolWrapper(Tool):
    """Wrap one remote MCP tool as a normal ZingClaw tool."""

    _plugin_discoverable = False
    _scopes: set[str] = set()

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        parameters: dict[str, Any] | None,
        executor: Any,
        *,
        read_only: bool = False,
        runtime: MCPRuntime | None = None,
        tool_timeout: int = 30,
    ) -> None:
        self._server_name = server_name
        self._original_name = tool_name
        self._name = sanitize_mcp_name(f"mcp_{server_name}_{tool_name}")
        self._description = description or tool_name
        self._parameters = _normalize_schema_for_openai(
            parameters or {"type": "object", "properties": {}, "required": []}
        )
        self._executor = executor
        self._read_only = read_only
        self._runtime = runtime
        self._tool_timeout = tool_timeout

    @classmethod
    def from_mcp_tool(cls, runtime: MCPRuntime, server_name: str, tool_def: Any) -> MCPToolWrapper:
        annotations = getattr(tool_def, "annotations", None)
        read_only = bool(getattr(annotations, "readOnlyHint", False))
        return cls(
            server_name=server_name,
            tool_name=tool_def.name,
            description=tool_def.description or tool_def.name,
            parameters=tool_def.inputSchema,
            executor=None,
            read_only=read_only,
            runtime=runtime,
            tool_timeout=runtime.tool_timeout_for(server_name),
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return self._read_only

    async def execute(self, **kwargs: Any) -> Any:
        if self._runtime is None:
            result = self._executor(**kwargs)
            if inspect.isawaitable(result):
                return await result
            if isinstance(result, asyncio.Future):
                return await result
            return result

        try:
            result = await self._runtime.call_tool(
                self._server_name,
                self._original_name,
                kwargs,
                timeout_s=self._tool_timeout,
            )
        except asyncio.TimeoutError:
            return f"Error: MCP tool '{self._name}' timed out after {self._tool_timeout}s"
        except Exception as exc:
            return f"Error: MCP tool '{self._name}' failed: {type(exc).__name__}: {exc}"

        parts = [_render_content_block(block) for block in result.content]
        rendered = "\n".join(part for part in parts if part.strip()).strip()
        if result.isError:
            return f"Error: {rendered or 'MCP tool reported an error.'}"
        return rendered or "(no output)"


class MCPResourceWrapper(Tool):
    """Expose one MCP resource URI as a read-only tool."""

    _plugin_discoverable = False
    _scopes: set[str] = set()

    def __init__(self, runtime: MCPRuntime, server_name: str, resource_def: Any) -> None:
        self._runtime = runtime
        self._server_name = server_name
        self._uri = resource_def.uri
        resource_label = getattr(resource_def, "name", None) or str(resource_def.uri)
        self._name = sanitize_mcp_name(
            f"mcp_{server_name}_resource_{resource_label}"
        )
        description = getattr(resource_def, "description", None) or resource_label
        self._description = f"[MCP Resource] {description}\nURI: {resource_def.uri}"
        self._parameters = {"type": "object", "properties": {}, "required": []}

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        del kwargs
        timeout_s = self._runtime.tool_timeout_for(self._server_name)
        try:
            result = await self._runtime.read_resource(
                self._server_name,
                self._uri,
                timeout_s=timeout_s,
            )
        except asyncio.TimeoutError:
            return f"Error: MCP resource '{self._name}' timed out after {timeout_s}s"
        except Exception as exc:
            return f"Error: MCP resource '{self._name}' failed: {type(exc).__name__}: {exc}"

        parts = [_render_resource_contents(content) for content in result.contents]
        rendered = "\n".join(part for part in parts if part.strip()).strip()
        return rendered or "(no output)"


class MCPPromptWrapper(Tool):
    """Expose one MCP prompt as a read-only tool."""

    _plugin_discoverable = False
    _scopes: set[str] = set()

    def __init__(self, runtime: MCPRuntime, server_name: str, prompt_def: Any) -> None:
        self._runtime = runtime
        self._server_name = server_name
        self._prompt_name = prompt_def.name
        self._name = sanitize_mcp_name(f"mcp_{server_name}_prompt_{prompt_def.name}")
        description = prompt_def.description or prompt_def.name
        self._description = f"[MCP Prompt] {description}"
        properties: dict[str, Any] = {}
        required: list[str] = []
        for argument in prompt_def.arguments or []:
            properties[argument.name] = {
                "type": "string",
                "description": argument.description or argument.name,
            }
            if argument.required:
                required.append(argument.name)
        self._parameters = {"type": "object", "properties": properties, "required": required}

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        timeout_s = self._runtime.tool_timeout_for(self._server_name)
        string_args = {key: str(value) for key, value in kwargs.items() if value is not None}
        try:
            result = await self._runtime.get_prompt(
                self._server_name,
                self._prompt_name,
                string_args,
                timeout_s=timeout_s,
            )
        except asyncio.TimeoutError:
            return f"Error: MCP prompt '{self._name}' timed out after {timeout_s}s"
        except Exception as exc:
            return f"Error: MCP prompt '{self._name}' failed: {type(exc).__name__}: {exc}"

        sections: list[str] = []
        if result.description:
            sections.append(result.description)
        for message in result.messages:
            role = str(getattr(message, "role", "user"))
            content = _render_content_block(message.content)
            sections.append(f"{role}: {content}")
        return "\n".join(section for section in sections if section.strip()) or "(no output)"


class MCPRuntime:
    """Manage MCP server connections and register their capabilities into one registry."""

    def __init__(self, settings: Settings, *, workspace: Path | None = None) -> None:
        self.settings = settings
        self.workspace = (workspace or settings.workspace_dir).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._servers = {
            cfg.name: cfg
            for cfg in settings.mcp_servers
            if cfg.enabled and cfg.name.strip()
        }
        self._connected: dict[str, _ConnectedServer] = {}
        self._registered_capabilities: dict[str, list[str]] = {}
        self._registered_details: dict[str, dict[str, list[str]]] = {}
        self._last_errors: dict[str, str] = {}
        self._registry: ToolRegistry | None = None
        self._lock = asyncio.Lock()

    @property
    def configured_server_names(self) -> set[str]:
        return set(self._servers)

    @property
    def connected_server_names(self) -> set[str]:
        return set(self._connected)

    def has_configured_servers(self) -> bool:
        return bool(self._servers)

    def tool_timeout_for(self, server_name: str) -> int:
        cfg = self._servers.get(server_name)
        return int(cfg.tool_timeout if cfg is not None else 30)

    async def connect_missing_servers(self, registry: ToolRegistry) -> None:
        """Connect all configured servers that are not live yet."""
        if not self._servers:
            return
        self._registry = registry
        async with self._lock:
            for name, cfg in self._servers.items():
                if name in self._connected:
                    continue
                await self._connect_server(name, cfg, registry=registry, register_wrappers=True)

    async def reload(
        self,
        settings: Settings | None = None,
        *,
        registry: ToolRegistry | None = None,
    ) -> dict[str, Any]:
        """Reconcile live MCP connections with a refreshed settings object."""
        target_settings = settings or self.settings
        target_registry = registry or self._registry
        if target_registry is None:
            raise RuntimeError("MCP runtime cannot reload before a ToolRegistry is attached")

        next_workspace = target_settings.workspace_dir.expanduser().resolve()
        next_workspace.mkdir(parents=True, exist_ok=True)
        next_servers = {
            cfg.name: cfg
            for cfg in target_settings.mcp_servers
            if cfg.enabled and cfg.name.strip()
        }

        async with self._lock:
            self._registry = target_registry
            current_servers = dict(self._servers)
            current_names = set(current_servers)
            next_names = set(next_servers)

            removed = sorted(current_names - next_names)
            added = sorted(next_names - current_names)
            changed = sorted(
                name
                for name in (current_names & next_names)
                if _server_signature(current_servers[name]) != _server_signature(next_servers[name])
            )
            retry_missing = sorted(
                name
                for name in next_names
                if name not in self._connected and name not in set(added) | set(changed)
            )

            tools_removed = 0
            for name in [*removed, *changed]:
                tools_removed += self._unregister_server_tools(name, target_registry)
                self._registered_capabilities.pop(name, None)
                self._registered_details.pop(name, None)
                await self._close_server(name)

            self.settings = target_settings
            self.workspace = next_workspace
            self._servers = next_servers

            to_connect = sorted(set(added) | set(changed) | set(retry_missing))
            failed: list[str] = []
            for name in to_connect:
                await self._connect_server(
                    name,
                    next_servers[name],
                    registry=target_registry,
                    register_wrappers=True,
                )
                if name not in self._connected:
                    failed.append(name)

            unchanged = not removed and not added and not changed and not retry_missing
            if failed:
                message = "MCP config reloaded, but some servers did not connect: " + ", ".join(failed)
            elif unchanged:
                message = "MCP config is already live."
            elif retry_missing and not added and not changed and not removed:
                message = "MCP connections refreshed without restarting ZingClaw."
            else:
                message = "MCP config reloaded without restarting ZingClaw."

            return {
                "ok": not failed,
                "message": message,
                "added": added,
                "changed": changed,
                "removed": removed,
                "retried": retry_missing,
                "recovered": sorted(name for name in to_connect if name in self._connected),
                "connected": sorted(self._connected),
                "configured": sorted(self._servers),
                "failed": failed,
                "errors": {name: self._last_errors.get(name, "") for name in failed},
                "tools_removed": tools_removed,
            }

    async def reconnect(
        self,
        server_name: str | None = None,
        *,
        registry: ToolRegistry | None = None,
    ) -> dict[str, Any]:
        """Reconnect one MCP server or all configured servers without reloading config."""
        target_registry = registry or self._registry
        if target_registry is None:
            raise RuntimeError("MCP runtime cannot reconnect before a ToolRegistry is attached")

        async with self._lock:
            self._registry = target_registry
            names = [server_name] if server_name else sorted(self._servers)
            missing = [name for name in names if name not in self._servers]
            attempted: list[str] = []
            recovered: list[str] = []
            failed: list[str] = []
            for name in names:
                cfg = self._servers.get(name)
                if cfg is None:
                    continue
                attempted.append(name)
                self._unregister_server_tools(name, target_registry)
                self._registered_capabilities.pop(name, None)
                await self._close_server(name)
                await self._connect_server(
                    name,
                    cfg,
                    registry=target_registry,
                    register_wrappers=True,
                )
                if name in self._connected:
                    recovered.append(name)
                else:
                    failed.append(name)

            if missing:
                message = "Some requested MCP servers are not configured: " + ", ".join(missing)
            elif failed:
                message = "MCP reconnect finished, but some servers are still unavailable: " + ", ".join(failed)
            else:
                target = server_name or "all configured servers"
                message = f"MCP reconnect succeeded for {target}."

            return {
                "ok": not failed and not missing,
                "message": message,
                "attempted": attempted,
                "recovered": recovered,
                "failed": failed,
                "missing": missing,
                "errors": {name: self._last_errors.get(name, "") for name in failed + missing},
                "connected": sorted(self._connected),
            }

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_s: int,
    ) -> Any:
        return await self._call_with_reconnect(
            server_name,
            timeout_s=timeout_s,
            operation=lambda session: session.call_tool(
                tool_name,
                arguments=arguments,
                read_timeout_seconds=timedelta(seconds=timeout_s),
            ),
        )

    async def read_resource(self, server_name: str, uri: Any, *, timeout_s: int) -> Any:
        return await self._call_with_reconnect(
            server_name,
            timeout_s=timeout_s,
            operation=lambda session: session.read_resource(uri),
        )

    async def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        arguments: dict[str, str],
        *,
        timeout_s: int,
    ) -> Any:
        return await self._call_with_reconnect(
            server_name,
            timeout_s=timeout_s,
            operation=lambda session: session.get_prompt(prompt_name, arguments=arguments),
        )

    def runtime_lines(self) -> list[str]:
        """Return model-visible runtime notes about configured MCP servers."""
        if not self._servers:
            return []

        lines: list[str] = []
        for name in sorted(self._servers):
            cfg = self._servers[name]
            prefix = _tool_prefix(name)
            if name in self._connected:
                capability_names = self._registered_capabilities.get(name, [])
                lines.append(
                    "MCP Server: "
                    f"{name} (connected; transport={cfg.transport}; "
                    f"capabilities={len(capability_names)}; tool_prefix={prefix})."
                )
                continue

            error = self._last_errors.get(name)
            if error:
                lines.append(
                    "MCP Server: "
                    f"{name} (unavailable; transport={cfg.transport}; last_error={error})."
                )
            else:
                lines.append(
                    "MCP Server: "
                    f"{name} (configured but not connected yet; transport={cfg.transport}; "
                    f"tool_prefix={prefix})."
                )
        return lines

    def status(self) -> list[MCPServerStatus]:
        items: list[MCPServerStatus] = []
        for name in sorted(self._servers):
            cfg = self._servers[name]
            connected = self._connected.get(name)
            capability_names = list(self._registered_capabilities.get(name, []))
            detail_names = self._registered_details.get(name, {})
            items.append(
                MCPServerStatus(
                    name=name,
                    transport=cfg.transport,
                    connected=connected is not None,
                    tool_prefix=_tool_prefix(name),
                    target=cfg.url or cfg.command or "",
                    tool_count=0 if connected is None else connected.tool_count,
                    resource_count=0 if connected is None else connected.resource_count,
                    prompt_count=0 if connected is None else connected.prompt_count,
                    capability_count=len(capability_names),
                    capabilities=capability_names,
                    tool_names=list(detail_names.get("tool_names", [])) if connected is None else list(connected.tool_names),
                    resource_names=list(detail_names.get("resource_names", [])) if connected is None else list(connected.resource_names),
                    prompt_names=list(detail_names.get("prompt_names", [])) if connected is None else list(connected.prompt_names),
                    error=self._last_errors.get(name),
                )
            )
        return items

    async def aclose(self) -> None:
        async with self._lock:
            for name in list(self._connected):
                await self._close_server(name)

    async def _call_with_reconnect(
        self,
        server_name: str,
        *,
        timeout_s: int,
        operation: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        last_exc: Exception | None = None
        for attempt in range(2):
            connected = self._connected.get(server_name)
            if connected is None:
                cfg = self._servers.get(server_name)
                if cfg is None:
                    raise RuntimeError(f"MCP server '{server_name}' is not configured")
                if self._registry is None:
                    raise RuntimeError("MCP runtime has not been connected to a ToolRegistry yet")
                async with self._lock:
                    if server_name not in self._connected:
                        await self._connect_server(
                            server_name,
                            cfg,
                            registry=self._registry,
                            register_wrappers=not self._registered_capabilities.get(server_name),
                        )
                connected = self._connected.get(server_name)
                if connected is None:
                    raise RuntimeError(
                        self._last_errors.get(server_name)
                        or f"Failed to connect MCP server '{server_name}'"
                    )

            try:
                return await asyncio.wait_for(operation(connected.session), timeout=timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_exc = exc
                self._last_errors[server_name] = f"{type(exc).__name__}: {exc}"
                if attempt >= 1:
                    break
                logger.warning(
                    "MCP server '{}' call failed ({}), reconnecting once",
                    server_name,
                    type(exc).__name__,
                )
                async with self._lock:
                    await self._close_server(server_name)
                    cfg = self._servers.get(server_name)
                    if cfg is None or self._registry is None:
                        break
                    await self._connect_server(
                        server_name,
                        cfg,
                        registry=self._registry,
                        register_wrappers=False,
                    )
        assert last_exc is not None
        raise last_exc

    async def _connect_server(
        self,
        name: str,
        cfg: MCPServerConfig,
        *,
        registry: ToolRegistry,
        register_wrappers: bool,
    ) -> None:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.client.streamable_http import streamable_http_client

        stack = AsyncExitStack()
        try:
            if cfg.transport == "stdio":
                if not cfg.command:
                    raise ValueError(f"MCP server '{name}' is missing command for stdio transport")
                env = dict(os.environ)
                env.update(cfg.env)
                command, args, env = _normalize_windows_stdio_command(cfg.command, cfg.args, env)
                params = StdioServerParameters(
                    command=command,
                    args=args,
                    env=env,
                    cwd=self.workspace,
                )
                read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
            elif cfg.transport in {"http", "streamable_http"}:
                if not cfg.url:
                    raise ValueError(
                        f"MCP server '{name}' is missing url for streamable_http transport"
                    )
                if not await _probe_http_url(cfg.url):
                    raise ConnectionError(f"MCP server '{name}' is unreachable at {cfg.url}")
                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=cfg.headers or None,
                        timeout=httpx.Timeout(cfg.tool_timeout, connect=min(cfg.tool_timeout, 10)),
                    )
                )
                read_stream, write_stream, _session_id = await stack.enter_async_context(
                    streamable_http_client(
                        cfg.url,
                        http_client=http_client,
                    )
                )
            elif cfg.transport == "sse":
                if not cfg.url:
                    raise ValueError(f"MCP server '{name}' is missing url for sse transport")
                if not await _probe_http_url(cfg.url):
                    raise ConnectionError(f"MCP server '{name}' is unreachable at {cfg.url}")
                read_stream, write_stream = await stack.enter_async_context(
                    sse_client(
                        cfg.url,
                        headers=cfg.headers or None,
                        timeout=min(cfg.tool_timeout, 10),
                        sse_read_timeout=max(cfg.tool_timeout, 30),
                    )
                )
            else:
                raise ValueError(f"Unsupported MCP transport: {cfg.transport}")

            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            wrappers, counts = await self._build_wrappers(name, session, cfg)
            if register_wrappers:
                for wrapper in wrappers:
                    registry.register(wrapper)
                self._registered_capabilities[name] = [wrapper.name for wrapper in wrappers]
            elif name not in self._registered_capabilities:
                self._registered_capabilities[name] = [wrapper.name for wrapper in wrappers]
            self._registered_details[name] = {
                "tool_names": list(counts["tool_names"]),
                "resource_names": list(counts["resource_names"]),
                "prompt_names": list(counts["prompt_names"]),
            }

            self._connected[name] = _ConnectedServer(
                config=cfg,
                stack=stack,
                session=session,
                capability_names=[wrapper.name for wrapper in wrappers],
                tool_count=counts["tool_count"],
                resource_count=counts["resource_count"],
                prompt_count=counts["prompt_count"],
                tool_names=counts["tool_names"],
                resource_names=counts["resource_names"],
                prompt_names=counts["prompt_names"],
            )
            self._last_errors.pop(name, None)
            logger.info(
                "MCP server '{}' connected with {} capabilities",
                name,
                len(self._registered_capabilities.get(name, [])),
            )
        except Exception as exc:
            self._last_errors[name] = f"{type(exc).__name__}: {exc}"
            with suppress(Exception):
                await stack.aclose()
            logger.warning("MCP server '{}' failed to connect: {}", name, self._last_errors[name])

    async def _build_wrappers(
        self,
        server_name: str,
        session: Any,
        cfg: MCPServerConfig,
    ) -> tuple[list[Tool], dict[str, int]]:
        enabled = {item.strip() for item in cfg.enabled_tools if item.strip()}
        allow_all = not enabled or "*" in enabled
        wrappers: list[Tool] = []
        counts = {
            "tool_count": 0,
            "resource_count": 0,
            "prompt_count": 0,
            "tool_names": [],
            "resource_names": [],
            "prompt_names": [],
        }

        tools_result = await session.list_tools()
        for tool_def in tools_result.tools:
            raw_name = tool_def.name
            wrapped_name = sanitize_mcp_name(f"mcp_{server_name}_{raw_name}")
            if not allow_all and raw_name not in enabled and wrapped_name not in enabled:
                continue
            wrapper = MCPToolWrapper.from_mcp_tool(self, server_name, tool_def)
            wrappers.append(wrapper)
            counts["tool_count"] += 1
            counts["tool_names"].append(wrapper.name)

        try:
            resources_result = await session.list_resources()
        except Exception:
            resources_result = None
        if resources_result is not None:
            for resource_def in resources_result.resources:
                wrapper = MCPResourceWrapper(self, server_name, resource_def)
                wrappers.append(wrapper)
                counts["resource_count"] += 1
                counts["resource_names"].append(wrapper.name)

        try:
            prompts_result = await session.list_prompts()
        except Exception:
            prompts_result = None
        if prompts_result is not None:
            for prompt_def in prompts_result.prompts:
                wrapper = MCPPromptWrapper(self, server_name, prompt_def)
                wrappers.append(wrapper)
                counts["prompt_count"] += 1
                counts["prompt_names"].append(wrapper.name)

        return wrappers, counts

    def _unregister_server_tools(self, server_name: str, registry: ToolRegistry) -> int:
        removed = 0
        for tool_name in list(self._registered_capabilities.get(server_name, [])):
            if registry.has(tool_name):
                registry.unregister(tool_name)
                removed += 1
        if removed:
            return removed

        prefix = _tool_prefix(server_name)
        for tool_name in list(registry.tool_names):
            if tool_name.startswith(prefix):
                registry.unregister(tool_name)
                removed += 1
        return removed

    async def _close_server(self, server_name: str) -> None:
        connected = self._connected.pop(server_name, None)
        if connected is None:
            return
        try:
            await connected.stack.aclose()
        except Exception:
            logger.debug("MCP server '{}' cleanup raised during close", server_name)


def _server_signature(cfg: MCPServerConfig) -> Any:
    return cfg.model_dump(mode="json")
