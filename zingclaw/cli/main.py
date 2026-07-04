"""CLI entrypoint for the current ZingClaw P0 runtime."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
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
from zingclaw.evaluation import (
    CurationResult,
    ReplayCase,
    ReplayDatasetResult,
    SuiteRunResult,
    TaskRunSuiteResult,
    TraceQuestionRunResult,
    TraceStore,
    curate_traces,
    load_question_set,
    organize_replay_dataset,
    run_replay_case,
    run_replay_suite,
    run_task_suite,
    run_question_set,
    write_starter_question_set,
)
from zingclaw.gateway import GatewayRuntime
from zingclaw.memory import DreamConsolidator
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
eval_app = typer.Typer(help="Run trace/replay evaluation cases.")
trace_app = typer.Typer(help="Generate plain question datasets and traces.")
memory_app = typer.Typer(help="Inspect and update long-term memory.")
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
            if item.tool_names:
                console.print(f"[cyan]{item.name}[/cyan] tools: {', '.join(item.tool_names)}")
            if item.resource_names:
                console.print(f"[cyan]{item.name}[/cyan] resources: {', '.join(item.resource_names)}")
            if item.prompt_names:
                console.print(f"[cyan]{item.name}[/cyan] prompts: {', '.join(item.prompt_names)}")


def _print_mcp_reload_result(result: dict[str, Any]) -> None:
    style = "green" if result.get("ok") else "yellow"
    console.print(f"[{style}]{result.get('message', 'MCP reload finished.')}[/{style}]")


def _print_scorecard_table(scorecard: Any, *, trace_dir: Path) -> None:
    style = "green" if scorecard.passed else "red"
    console.print(f"[{style}]Replay {scorecard.case_name}: score {scorecard.score}/100[/{style}]")
    console.print(f"[dim]trace: {trace_dir}[/dim]")

    table = Table(title="Scorecard")
    table.add_column("Check", style="cyan")
    table.add_column("Pass", style="white")
    table.add_column("Detail", style="white")
    for check in scorecard.checks:
        table.add_row(check.name, "yes" if check.passed else "no", check.detail)
    console.print(table)


def _print_suite_result(result: SuiteRunResult) -> None:
    style = "green" if result.failed == 0 else "red"
    console.print(
        f"[{style}]Eval suite: {result.passed}/{result.total} passed "
        f"({result.pass_rate:.2f}%)[/{style}]"
    )
    console.print(f"[dim]json report: {result.report_path}[/dim]")
    console.print(f"[dim]readable report: {result.readable_report_path}[/dim]")

    table = Table(title="Eval Suite")
    table.add_column("Case", style="cyan")
    table.add_column("Pass", style="white")
    table.add_column("Score", justify="right")
    table.add_column("Trace", style="white")
    table.add_column("Failure", style="red")
    for item in result.cases:
        failure = item.error or "; ".join(item.failed_checks)
        table.add_row(
            item.case_name,
            "yes" if item.passed else "no",
            str(item.score),
            item.trace_dir or "-",
            _clip(failure, 180) if failure else "",
        )
    console.print(table)


def _print_task_run_result(result: TaskRunSuiteResult) -> None:
    style = "green" if result.failed == 0 else "red"
    console.print(
        f"[{style}]Task eval: {result.passed}/{result.total} passed "
        f"({result.pass_rate:.2f}%)[/{style}]"
    )
    console.print(f"[dim]run dir: {result.run_dir}[/dim]")
    console.print(f"[dim]json report: {result.report_path}[/dim]")
    console.print(f"[dim]readable report: {result.readable_report_path}[/dim]")

    table = Table(title="Task Eval")
    table.add_column("Task", style="cyan")
    table.add_column("Pass", style="white")
    table.add_column("Score", justify="right")
    table.add_column("Tools", style="white")
    table.add_column("Trace", style="white")
    table.add_column("Failure", style="red")
    for item in result.cases:
        failure = item.error or "; ".join(item.failed_checks)
        table.add_row(
            item.task_id,
            "yes" if item.passed else "no",
            str(item.score),
            _clip(", ".join(item.tools), 80) if item.tools else "-",
            item.trace_dir or "-",
            _clip(failure, 180) if failure else "",
        )
    console.print(table)


def _print_curation_result(result: CurationResult) -> None:
    counts = result.counts
    console.print(
        "[green]Curated traces:[/green] "
        f"{counts['regression']} regression, "
        f"{counts['candidate']} candidate, "
        f"{counts['discard']} discarded"
    )
    console.print(f"[dim]report: {result.report_path}[/dim]")

    table = Table(title="Trace Curation")
    table.add_column("Trace", style="cyan")
    table.add_column("Decision", style="white")
    table.add_column("Score", justify="right")
    table.add_column("Tools", style="white")
    table.add_column("Reason", style="white")
    table.add_column("Case", style="white")
    for item in result.items:
        table.add_row(
            item.trace_id,
            item.decision,
            str(item.score),
            ", ".join(item.tools_used) if item.tools_used else "-",
            "; ".join(item.reasons),
            item.case_path or "-",
        )
    console.print(table)


def _print_replay_dataset_result(result: ReplayDatasetResult) -> None:
    counts = result.counts
    style = "green" if counts["issues"] == 0 and not result.warnings else "yellow"
    console.print(
        f"[{style}]Replay dataset: {counts['regression']} regression, "
        f"{counts['candidate']} candidate, {counts['issues']} issues[/{style}]"
    )
    console.print(f"[dim]manifest: {result.manifest_path}[/dim]")
    if result.normalized:
        console.print("[dim]case metadata normalized in place[/dim]")
    if result.refreshed:
        console.print("[dim]inferred metadata refreshed[/dim]")

    table = Table(title="Replay Dataset")
    table.add_column("Case", style="cyan")
    table.add_column("Split", style="white")
    table.add_column("Category", style="white")
    table.add_column("Tags", style="white")
    table.add_column("Issues", style="red")
    for item in result.items:
        table.add_row(
            item.name,
            item.split,
            item.category or "-",
            _clip(", ".join(item.tags), 120) if item.tags else "-",
            _clip("; ".join(item.issues), 160) if item.issues else "",
        )
    console.print(table)
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")


def _print_trace_question_run_table(results: list[TraceQuestionRunResult], *, traces_dir: Path) -> None:
    table = Table(title="Trace Question Runs")
    table.add_column("Question", style="cyan")
    table.add_column("Category", style="white")
    table.add_column("Stop", style="white")
    table.add_column("Tools", style="white")
    table.add_column("Trace", style="white")
    table.add_column("Error", style="red")
    for item in results:
        trace_path = str(traces_dir / item.trace_id / "trace.jsonl") if item.trace_id else "-"
        table.add_row(
            item.question.id,
            item.question.category,
            item.stop_reason,
            ", ".join(item.tools_used) if item.tools_used else "-",
            trace_path,
            item.error or "",
        )
    console.print(table)


def _clip(value: Any, limit: int = 160) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _trace_event_detail(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "")
    if event_type == "user_input":
        return _clip(event.get("input"), 180)
    if event_type == "turn_started":
        return "started"
    if event_type == "status":
        detail = str(event.get("status") or "")
        if event.get("iteration") is not None:
            detail += f" iter={event['iteration']}"
        if event.get("detail"):
            detail += f" ({event['detail']})"
        return detail
    if event_type == "tool_started":
        return f"{event.get('tool_name')} start args={_clip(event.get('arguments'), 140)}"
    if event_type == "tool_completed":
        state = "error" if event.get("is_error") else "done"
        return f"{event.get('tool_name')} {state}: {_clip(event.get('result_preview'), 140)}"
    if event_type == "turn_completed":
        tools = ", ".join(str(tool) for tool in event.get("tools_used") or []) or "-"
        detail = f"{event.get('stop_reason') or 'completed'}; tools={tools}"
        if event.get("latency_ms") is not None:
            detail += f"; latency={event['latency_ms']}ms"
        if event.get("error"):
            detail += f"; error={event['error']}"
        return detail
    if event_type == "turn_result":
        tools = ", ".join(str(tool) for tool in event.get("tools_used") or []) or "-"
        detail = f"result {event.get('stop_reason') or '(unknown)'}; tools={tools}"
        if event.get("error"):
            detail += f"; error={event['error']}"
        return detail
    if event_type == "text_delta":
        return _clip(event.get("delta"), 120)
    return _clip(event, 180)


def _trace_final_answer(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("type") == "turn_completed":
            return str(event.get("final_content") or "").strip()
    for event in reversed(events):
        if event.get("type") == "turn_result":
            return str(event.get("final_content") or "").strip()
    return ""


def _print_trace_inspection(
    *,
    trace_ref: str,
    metadata: dict[str, Any],
    events: list[dict[str, Any]],
    final_chars: int,
) -> None:
    console.print(f"[bold]Trace[/bold] {metadata.get('trace_id') or trace_ref}")
    summary = Table(show_header=False, box=None)
    summary.add_column("Key", style="cyan")
    summary.add_column("Value", style="white")
    for key in ("workflow_name", "session_key", "channel", "chat_id", "model", "provider", "status", "error"):
        value = metadata.get(key)
        if value not in (None, ""):
            summary.add_row(key, _clip(value, 220))
    summary.add_row("events", str(len(events)))
    console.print(summary)

    timeline = Table(title="Trace Timeline")
    timeline.add_column("#", style="dim", justify="right")
    timeline.add_column("Elapsed", style="cyan", justify="right")
    timeline.add_column("Event", style="white")
    timeline.add_column("Detail", style="white")
    for event in events:
        elapsed = event.get("elapsed_ms")
        timeline.add_row(
            str(event.get("seq") or ""),
            f"{elapsed}ms" if elapsed is not None else "",
            str(event.get("type") or ""),
            _trace_event_detail(event),
        )
    console.print(timeline)

    final_answer = _trace_final_answer(events)
    if final_answer:
        console.print("[bold]Final Answer[/bold]")
        console.print(_clip(final_answer, max(1, final_chars)))


def _print_dream_result(result: Any) -> None:
    if result.processed_entries <= 0:
        console.print("[dim]No unprocessed archived history entries for Dream.[/dim]")
        return
    style = "green" if result.memory_updated else "yellow"
    status = "updated MEMORY.md" if result.memory_updated else "nothing durable to save"
    console.print(
        f"[{style}]Dream processed {result.processed_entries} archived entries "
        f"through cursor {result.last_cursor}: {status}[/{style}]"
    )
    if result.content:
        console.print(result.content)


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


@memory_app.command("dream")
def memory_dream(
    max_entries: int = typer.Option(
        20,
        "--max-entries",
        help="Maximum archived history entries to process in one Dream run.",
    ),
) -> None:
    """Extract durable long-term memory from archived history entries."""
    settings = get_settings()
    ensure_runtime_dirs(settings)
    loop = AgentLoop.from_settings(settings)
    try:
        dream = DreamConsolidator(
            loop.memory,
            loop.provider,
            model=settings.model_name,
            max_entries=max_entries,
        )
        result = asyncio.run(dream.run_once())
        _print_dream_result(result)
    finally:
        loop.close()


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


@mcp_app.command("reconnect")
def mcp_reconnect(server: str = typer.Argument("", help="Optional MCP server name.")) -> None:
    """Reconnect one MCP server or all configured servers without reloading config."""
    settings = get_settings()
    ensure_runtime_dirs(settings)
    loop = AgentLoop.from_settings(settings)
    try:
        result = asyncio.run(loop.reconnect_mcp(server.strip() or None))
        _print_mcp_reload_result(result)
        statuses = asyncio.run(loop.get_mcp_status(connect_missing=False))
        _print_mcp_status_table(statuses, title="MCP Status After Reconnect", verbose=True)
    finally:
        loop.close()


@eval_app.command("run")
def eval_run(
    case: Path = typer.Argument(..., help="Path to a replay case JSON file."),
    trace_root: Path | None = typer.Option(
        None,
        "--trace-root",
        help="Directory where fresh traces are stored. Defaults to ZINGCLAW_HOME/traces.",
    ),
    fail_on_regression: bool = typer.Option(
        True,
        "--fail-on-regression/--no-fail-on-regression",
        help="Exit with code 1 when the scorecard fails.",
    ),
) -> None:
    """Replay one evaluation case and write a fresh trace plus scorecard."""
    settings = get_settings()
    ensure_runtime_dirs(settings)
    replay_case = ReplayCase.from_file(case)
    store = TraceStore(trace_root or (settings.home_dir / "traces"))
    loop = AgentLoop.from_settings(settings)
    try:
        result = asyncio.run(run_replay_case(loop, replay_case, store))
        _print_scorecard_table(result.scorecard, trace_dir=result.trace_dir)
        if fail_on_regression and not result.scorecard.passed:
            raise typer.Exit(code=1)
    finally:
        loop.close()


@eval_app.command("run-suite")
def eval_run_suite(
    suite: Path = typer.Argument(..., help="Replay case file or directory."),
    trace_root: Path | None = typer.Option(
        None,
        "--trace-root",
        help="Directory where fresh traces are stored. Defaults to ZINGCLAW_HOME/traces.",
    ),
    pattern: str = typer.Option(
        "*.json",
        "--pattern",
        help="Glob pattern used when SUITE is a directory.",
    ),
    report: Path | None = typer.Option(
        None,
        "--report",
        help="Where to write the aggregate suite report. Defaults to SUITE/suite_report.json.",
    ),
    readable_report: Path | None = typer.Option(
        None,
        "--readable-report",
        help="Where to write the human-readable Markdown report. Defaults beside --report.",
    ),
    fail_on_regression: bool = typer.Option(
        True,
        "--fail-on-regression/--no-fail-on-regression",
        help="Exit with code 1 when any suite case fails.",
    ),
) -> None:
    """Replay a directory of evaluation cases and write one aggregate report."""
    settings = get_settings()
    ensure_runtime_dirs(settings)
    store = TraceStore(trace_root or (settings.home_dir / "traces"))
    loop = AgentLoop.from_settings(settings)
    try:
        result = asyncio.run(
            run_replay_suite(
                loop,
                suite,
                store,
                pattern=pattern,
                report_path=report,
                readable_report_path=readable_report,
            )
        )
        _print_suite_result(result)
        if fail_on_regression and result.failed:
            raise typer.Exit(code=1)
    finally:
        loop.close()


@eval_app.command("task-run")
def eval_task_run(
    tasks: Path = typer.Argument(..., help="Task contract JSON file or directory."),
    fixtures_root: Path = typer.Option(
        Path("eval_fixtures"),
        "--fixtures-root",
        help="Directory containing named fixture workspaces.",
    ),
    output: Path = typer.Option(
        Path("eval_runs"),
        "--output",
        "-o",
        help="Directory where isolated task runs and reports are written.",
    ),
    pattern: str = typer.Option(
        "*.json",
        "--pattern",
        help="Glob pattern used when TASKS is a directory.",
    ),
    run_id: str = typer.Option(
        "",
        "--run-id",
        help="Optional stable run id. Defaults to timestamp plus random suffix.",
    ),
    fail_on_failed: bool = typer.Option(
        True,
        "--fail-on-failed/--no-fail-on-failed",
        help="Exit with code 1 when any task fails.",
    ),
) -> None:
    """Run structured task contracts in isolated fixture workspaces."""
    settings = get_settings()
    ensure_runtime_dirs(settings)
    result = asyncio.run(
        run_task_suite(
            settings,
            tasks,
            fixtures_root=fixtures_root,
            runs_root=output,
            pattern=pattern,
            run_id=run_id.strip() or None,
        )
    )
    _print_task_run_result(result)
    if fail_on_failed and result.failed:
        raise typer.Exit(code=1)


@eval_app.command("curate")
def eval_curate(
    traces: Path = typer.Argument(
        Path("traces"),
        help="Trace root, one trace directory, or one trace.jsonl path.",
    ),
    output: Path = typer.Option(
        Path("replay_cases"),
        "--output",
        "-o",
        help="Directory where regression/candidate replay cases are written.",
    ),
    include_candidates: bool = typer.Option(
        True,
        "--candidates/--no-candidates",
        help="Also write candidate replay cases for non-regression traces.",
    ),
) -> None:
    """Curate traces into deterministic replay cases using conservative rules."""
    result = curate_traces(
        traces,
        output_dir=output,
        include_candidates=include_candidates,
    )
    _print_curation_result(result)


@eval_app.command("organize")
def eval_organize(
    replay_cases: Path = typer.Argument(
        Path("replay_cases"),
        help="Replay case dataset root containing regression/ and candidates/.",
    ),
    manifest: Path | None = typer.Option(
        None,
        "--manifest",
        help="Where to write the dataset manifest. Defaults to REPLAY_CASES/manifest.json.",
    ),
    normalize: bool = typer.Option(
        False,
        "--normalize/--no-normalize",
        help="Fill missing category/tags/purpose/stability fields in case JSON files.",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh/--no-refresh",
        help="Recompute inferred category/tags/purpose/stability instead of only filling missing fields.",
    ),
) -> None:
    """Organize replay cases into a manifest and optionally normalize metadata."""
    try:
        result = organize_replay_dataset(
            replay_cases,
            manifest_path=manifest,
            normalize=normalize,
            refresh=refresh,
        )
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    _print_replay_dataset_result(result)


@trace_app.command("seed")
def trace_seed(
    output: Path = typer.Option(
        Path("eval_questions") / "starter.json",
        "--output",
        "-o",
        help="Where to write the starter plain-question dataset.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite the output file if it already exists."),
) -> None:
    """Generate representative plain questions for trace inspection."""
    path = output.expanduser().resolve()
    try:
        question_set = write_starter_question_set(path, overwrite=force)
    except FileExistsError:
        console.print(f"[yellow]{path} already exists. Use --force to overwrite.[/yellow]")
        raise typer.Exit(code=1) from None
    categories = sorted({question.category for question in question_set.questions})
    console.print(f"[green]Wrote {len(question_set.questions)} trace questions to {path}[/green]")
    console.print(f"[dim]categories: {', '.join(categories)}[/dim]")


@trace_app.command("inspect")
def trace_inspect(
    trace: str = typer.Argument(..., help="Trace id, trace directory, or trace.jsonl path."),
    trace_root: Path | None = typer.Option(
        None,
        "--trace-root",
        help="Trace root used when TRACE is an id. Defaults to tracing.tracesDir or ZINGCLAW_HOME/traces.",
    ),
    final_chars: int = typer.Option(
        1600,
        "--final-chars",
        help="Maximum characters to print from the final answer.",
    ),
) -> None:
    """Print a readable timeline for one trace without rerunning the agent."""
    settings = get_settings()
    ensure_runtime_dirs(settings)
    traces_dir = trace_root or settings.tracing.traces_dir or (settings.home_dir / "traces")
    store = TraceStore(traces_dir)
    events = store.read_events(trace)
    if not events:
        console.print(f"[red]No trace events found for {trace!r}.[/red]")
        raise typer.Exit(code=1)
    metadata = store.read_metadata(trace)
    _print_trace_inspection(
        trace_ref=trace,
        metadata=metadata,
        events=events,
        final_chars=max(1, int(final_chars)),
    )


@trace_app.command("run")
def trace_run(
    questions: Path = typer.Argument(
        Path("eval_questions") / "starter.json",
        help="Path to a plain-question dataset JSON file.",
    ),
    trace_root: Path | None = typer.Option(
        None,
        "--trace-root",
        help="Directory where traces are stored. Defaults to tracing.tracesDir or ZINGCLAW_HOME/traces.",
    ),
    category: list[str] | None = typer.Option(
        None,
        "--category",
        "-c",
        help="Only run questions from this category. Can be repeated.",
    ),
    question_id: list[str] | None = typer.Option(
        None,
        "--id",
        help="Only run this question id. Can be repeated.",
    ),
    tag: list[str] | None = typer.Option(
        None,
        "--tag",
        help="Only run questions containing this tag. Can be repeated.",
    ),
    offset: int = typer.Option(
        0,
        "--offset",
        help="Skip this many selected questions before running.",
    ),
    limit: int = typer.Option(0, "--limit", help="Maximum number of questions to run. 0 means all selected."),
    session_prefix: str = typer.Option(
        "traceq",
        "--session-prefix",
        help="Session prefix used to isolate question runs.",
    ),
) -> None:
    """Run plain questions through ZingClaw so each one produces a trace."""
    settings = get_settings()
    ensure_runtime_dirs(settings)
    if trace_root is not None:
        settings.tracing.traces_dir = trace_root.expanduser().resolve()
    traces_dir = settings.tracing.traces_dir or (settings.home_dir / "traces")
    traces_dir.mkdir(parents=True, exist_ok=True)
    question_set = load_question_set(questions)
    loop = AgentLoop.from_settings(settings)
    try:
        results = asyncio.run(
            run_question_set(
                loop,
                question_set,
                categories=set(category or []),
                ids=set(question_id or []),
                tags=set(tag or []),
                offset=max(0, int(offset)),
                limit=max(0, int(limit)),
                session_prefix=session_prefix,
            )
        )
    finally:
        loop.close()

    if not results:
        console.print("[yellow]No questions matched the selected filters.[/yellow]")
        raise typer.Exit(code=1)
    _print_trace_question_run_table(results, traces_dir=traces_dir)


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
app.add_typer(memory_app, name="memory")
app.add_typer(eval_app, name="eval")
app.add_typer(trace_app, name="trace")
