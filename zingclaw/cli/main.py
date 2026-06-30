"""CLI entrypoint for the current ZingClaw P0 runtime."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from zingclaw import __version__
from zingclaw.agent import AgentLoop
from zingclaw.bus import (
    InboundMessage,
    MessageBus,
    OutboundMessage,
    RuntimeEventBus,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    TurnOutputDelta,
    TurnRunStatusChanged,
)
from zingclaw.command import CommandContext, CommandRouter, build_help_text, register_builtin_commands
from zingclaw.config import ensure_runtime_dirs, get_settings
from zingclaw.gateway import GatewayRuntime
from zingclaw.tools import (
    MCPServerStatus,
    request_mcp_reload,
)

app = typer.Typer(
    name="zingclaw",
    add_completion=False,
    no_args_is_help=True,
    help="ZingClaw - transparent local agent runtime",
)
console = Console()
_EXIT_COMMANDS = {"/exit", "/quit", "exit", "quit"}
mcp_app = typer.Typer(help="Inspect or reload MCP runtime state.")
_CHAT_COMMANDS = CommandRouter()
register_builtin_commands(_CHAT_COMMANDS)


@dataclass(slots=True)
class ChatTurnResult:
    final_content: str | None
    stop_reason: str = "completed"
    error: str | None = None


@dataclass(slots=True)
class _CliRuntimeState:
    last_status: str | None = None
    started_tools: set[tuple[str, str | None]] = field(default_factory=set)


def _print_chat_result(result: Any, *, already_streamed: bool = False) -> None:
    if already_streamed:
        console.print()
    elif result.final_content:
        console.print(result.final_content)
    else:
        console.print("[dim](empty response)[/dim]")
    if result.stop_reason != "completed":
        console.print(f"[yellow]stop_reason:[/yellow] {result.stop_reason}")
    if result.error:
        console.print(f"[red]error:[/red] {result.error}")


def _print_chat_help() -> None:
    console.print(build_help_text(include_exit=True))


def _result_from_outbound(msg: OutboundMessage) -> ChatTurnResult:
    metadata = dict(msg.metadata or {})
    error = metadata.get("error")
    return ChatTurnResult(
        final_content=None if metadata.get("_streamed") else (msg.text or None),
        stop_reason=str(metadata.get("stop_reason") or "completed"),
        error=error if isinstance(error, str) else None,
    )


def _runtime_event_matches(event: Any, *, session_key: str) -> bool:
    context = getattr(event, "context", None)
    if context is None:
        return False
    return str(getattr(context, "channel", "")) == "cli" and str(
        getattr(context, "session_key", "")
    ) == session_key


def _runtime_event_line(event: Any, state: _CliRuntimeState) -> str | None:
    if isinstance(event, TurnOutputDelta):
        return None
    if isinstance(event, TurnCompleted):
        return None
    if isinstance(event, TurnRunStatusChanged):
        status = str(event.status or "").strip().lower()
        if status == state.last_status:
            return None
        state.last_status = status
        if status == "building_context":
            return "preparing context..."
        if status == "calling_model":
            return "thinking..."
        if status == "executing_tools":
            return "executing tools..."
        if status == "error":
            return f"runtime error: {event.detail or 'unknown error'}"
        return None
    if isinstance(event, ToolCallStarted):
        key = (event.tool_name, event.tool_call_id)
        if key in state.started_tools:
            return None
        state.started_tools.add(key)
        return f"tool: {event.tool_name}"
    if isinstance(event, ToolCallCompleted):
        if event.is_error:
            return f"tool error: {event.tool_name}"
        return f"tool done: {event.tool_name}"
    return None


def _print_runtime_event_line(line: str) -> None:
    console.print(f"[dim]{line}[/dim]")


def _drain_runtime_queue(
    runtime_queue: asyncio.Queue[Any] | None,
    runtime_state: _CliRuntimeState,
) -> None:
    if runtime_queue is None:
        return
    while True:
        try:
            payload = runtime_queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        line = _runtime_event_line(payload, runtime_state)
        if line:
            _print_runtime_event_line(line)


async def _run_one_turn_async(loop: AgentLoop, *, session_key: str, message: str) -> tuple[ChatTurnResult, bool]:
    bus = MessageBus()
    loop.bind_bus(bus)
    loop_task = asyncio.create_task(loop.run())
    streamed = False
    runtime_state = _CliRuntimeState()
    runtime_queue: asyncio.Queue[Any] | None = None
    unsubscribe = None

    runtime_events = getattr(loop, "runtime_events", None)
    if isinstance(runtime_events, RuntimeEventBus):
        runtime_queue = asyncio.Queue()

        def _on_runtime_event(event: Any) -> None:
            if _runtime_event_matches(event, session_key=session_key):
                runtime_queue.put_nowait(event)

        unsubscribe = runtime_events.subscribe(_on_runtime_event)

    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="cli",
                chat_id=session_key,
                session_key=session_key,
                sender_id="user",
                text=message,
                metadata={"_wants_stream": True},
            )
        )

        while True:
            outbound_task = asyncio.create_task(bus.consume_outbound())
            runtime_task = asyncio.create_task(runtime_queue.get()) if runtime_queue is not None else None
            wait_set = {outbound_task}
            if runtime_task is not None:
                wait_set.add(runtime_task)
            done, pending = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            outbound: OutboundMessage | None = None
            runtime_payloads: list[Any] = []
            for completed in done:
                payload = completed.result()
                if runtime_queue is not None and completed is runtime_task:
                    runtime_payloads.append(payload)
                else:
                    outbound = payload

            for payload in runtime_payloads:
                line = _runtime_event_line(payload, runtime_state)
                if line:
                    _print_runtime_event_line(line)

            if outbound is None:
                continue
            _drain_runtime_queue(runtime_queue, runtime_state)
            if outbound.channel != "cli" or outbound.session_key != session_key:
                continue
            if outbound.metadata.get("_stream_delta"):
                streamed = True
                sys.stdout.write(outbound.text)
                sys.stdout.flush()
                continue
            return _result_from_outbound(outbound), streamed
    finally:
        if callable(unsubscribe):
            unsubscribe()
        loop.stop()
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)


def _run_one_turn(loop: AgentLoop, *, session_key: str, message: str) -> Any:
    return asyncio.run(_run_one_turn_async(loop, session_key=session_key, message=message))


async def _run_mcp_reload_async(loop: AgentLoop) -> dict[str, Any]:
    bus = MessageBus()
    loop.bind_bus(bus)
    loop_task = asyncio.create_task(loop.run())
    try:
        return await request_mcp_reload(bus)
    finally:
        loop.stop()
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)


def _run_mcp_reload(loop: AgentLoop) -> dict[str, Any]:
    return asyncio.run(_run_mcp_reload_async(loop))


def _clear_session(loop: AgentLoop, session_key: str) -> None:
    session = loop.sessions.get_or_create(session_key)
    session.clear()
    loop.sessions.save(session)
    console.print(f"Cleared session `{session_key}`.")


async def _dispatch_chat_command_async(
    loop: AgentLoop,
    *,
    session_key: str,
    raw: str,
) -> OutboundMessage | None:
    session = loop.sessions.get_or_create(session_key)
    ctx = CommandContext(
        raw=raw,
        session_key=session_key,
        channel="cli",
        chat_id=session_key,
        loop=loop,
        session=session,
        metadata={"include_exit": True},
    )
    return await _CHAT_COMMANDS.dispatch(ctx)


def _dispatch_chat_command(loop: AgentLoop, *, session_key: str, raw: str) -> OutboundMessage | None:
    return asyncio.run(_dispatch_chat_command_async(loop, session_key=session_key, raw=raw))


def _print_command_outbound(msg: OutboundMessage) -> None:
    if msg.text:
        console.print(msg.text)
    else:
        console.print("[dim](empty response)[/dim]")


def _print_mcp_status_table(
    statuses: list[MCPServerStatus],
    *,
    title: str = "MCP Status",
    verbose: bool = False,
) -> None:
    table = Table(title=title)
    table.add_column("Server", style="cyan")
    table.add_column("Transport", style="white")
    table.add_column("State", style="white")
    table.add_column("Capabilities", style="white")
    table.add_column("Prefix", style="white")
    if verbose:
        table.add_column("Target", style="white")
    table.add_column("Error", style="red")

    if not statuses:
        cells = ["(none)", "-", "-", "-", "-"]
        if verbose:
            cells.append("-")
        cells.append("-")
        table.add_row(*cells)
    else:
        for item in statuses:
            state = "connected" if item.connected else "unavailable"
            cells = [
                item.name,
                item.transport,
                state,
                f"{item.capability_count} ({item.tool_count}/{item.resource_count}/{item.prompt_count})",
                item.tool_prefix,
            ]
            if verbose:
                cells.append(item.target)
            cells.append(item.error or "")
            table.add_row(*cells)
    console.print(table)
    if verbose and statuses:
        console.print("[dim]Capabilities format: total (tools/resources/prompts)[/dim]")
        for item in statuses:
            if item.capabilities:
                console.print(f"[cyan]{item.name}[/cyan] capabilities: {', '.join(item.capabilities)}")


def _print_mcp_reload_result(result: dict[str, Any]) -> None:
    style = "green" if result.get("ok") else "yellow"
    console.print(f"[{style}]{result.get('message', 'MCP reload finished.')}[/{style}]")


def _interactive_chat(loop: AgentLoop, *, session_key: str) -> None:
    console.print(f"model: {loop.settings.model_name}")
    console.print(f"session: {session_key}")
    console.print(f"workspace: {loop.settings.workspace_dir}")
    _print_chat_help()

    while True:
        for notice in loop.drain_cron_notifications(session_key):
            console.print()
            console.print(f"[magenta]{notice}[/magenta]")
        for notice in loop.drain_subagent_notifications(session_key):
            console.print()
            console.print(f"[cyan]{notice}[/cyan]")
        try:
            raw = input(f"[{session_key}]> ")
        except EOFError:
            console.print("\nExiting chat.")
            break
        except KeyboardInterrupt:
            console.print("\nInterrupted. Use `/exit` to quit cleanly.")
            continue

        message = raw.strip()
        if not message:
            continue
        if message in _EXIT_COMMANDS:
            console.print("Exiting chat.")
            break
        if _CHAT_COMMANDS.is_command(message):
            outbound = _dispatch_chat_command(loop, session_key=session_key, raw=message)
            if outbound is not None:
                _print_command_outbound(outbound)
            continue

        result, streamed = _run_one_turn(loop, session_key=session_key, message=message)
        _print_chat_result(result, already_streamed=streamed)


@app.command()
def status() -> None:
    """Print the current local runtime configuration."""
    settings = get_settings()
    ensure_runtime_dirs(settings)

    table = Table(title="ZingClaw Status")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("version", __version__)
    table.add_row("config", str(settings.resolved_config_path))
    table.add_row("provider", settings.provider_name)
    table.add_row("model", settings.model_name)
    table.add_row("workspace", str(settings.workspace_dir))
    table.add_row("sessions", str(settings.sessions_dir))
    table.add_row("memory", str(settings.memory_dir))
    table.add_row("logs", str(settings.logs_dir))
    table.add_row("skills", str(settings.skills_dir))
    table.add_row(
        "disabled skills",
        ", ".join(settings.agent.disabled_skills) if settings.agent.disabled_skills else "(none)",
    )
    table.add_row("api key", "configured" if settings.openai_api_key else "missing")
    table.add_row("api base", settings.openai_api_base or "(provider default)")
    table.add_row("channels", str(len(settings.channels)))
    table.add_row("mcp servers", str(len(settings.mcp_servers)))
    table.add_row("max iterations", str(settings.max_iterations))
    table.add_row("max tool chars", str(settings.max_tool_result_chars))
    console.print(table)


@mcp_app.command("status")
def mcp_status() -> None:
    """Show MCP server runtime status and registered capability counts."""
    settings = get_settings()
    ensure_runtime_dirs(settings)
    loop = AgentLoop.from_settings(settings)
    try:
        statuses = asyncio.run(loop.get_mcp_status())
        _print_mcp_status_table(statuses, verbose=True)
    finally:
        loop.close()


@mcp_app.command("reload")
def mcp_reload() -> None:
    """Reload MCP configuration and reconnect servers in this runtime."""
    settings = get_settings()
    ensure_runtime_dirs(settings)
    loop = AgentLoop.from_settings(settings)
    try:
        result = _run_mcp_reload(loop)
        _print_mcp_reload_result(result)
        statuses = asyncio.run(loop.get_mcp_status(connect_missing=False))
        _print_mcp_status_table(statuses, title="MCP Status After Reload", verbose=True)
    finally:
        loop.close()


@app.command()
def chat(
    message: str = typer.Option(
        "",
        "--message",
        "-m",
        help="Optional one-shot message. Omit to enter interactive chat mode.",
    ),
    session: str = typer.Option("default", "--session", "-s", help="Session key to use."),
) -> None:
    """Run interactive or one-shot chat using the current local runtime."""
    settings = get_settings()
    ensure_runtime_dirs(settings)
    loop = AgentLoop.from_settings(settings)
    try:
        stripped = message.strip()
        if stripped:
            if _CHAT_COMMANDS.is_command(stripped) and stripped not in _EXIT_COMMANDS:
                outbound = _dispatch_chat_command(loop, session_key=session, raw=stripped)
                if outbound is not None:
                    _print_command_outbound(outbound)
            else:
                result, streamed = _run_one_turn(loop, session_key=session, message=stripped)
                _print_chat_result(result, already_streamed=streamed)
        else:
            _interactive_chat(loop, session_key=session)
    finally:
        loop.close()


@app.command()
def gateway(
    host: str = typer.Option("", "--host", help="Gateway bind host. Defaults to api.host from config."),
    port: int = typer.Option(0, "--port", help="Gateway port. Defaults to api.port from config."),
) -> None:
    """Run the minimal HTTP gateway with API and runtime-event SSE endpoints."""
    try:
        web = import_module("aiohttp.web")
        create_app = import_module("zingclaw.api").create_app
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("aiohttp"):
            console.print("[red]aiohttp is required for `zingclaw gateway`.[/red]")
            console.print("Install it with `pip install aiohttp` or `pip install -e .`.")
            raise typer.Exit(code=1) from exc
        raise

    settings = get_settings()
    ensure_runtime_dirs(settings)
    loop = AgentLoop.from_settings(settings)
    runtime = GatewayRuntime(loop)
    app_host = host.strip() or settings.api.host
    app_port = int(port or settings.api.port)
    web.run_app(create_app(runtime, settings), host=app_host, port=app_port)


def main() -> None:
    app()


app.add_typer(mcp_app, name="mcp")
