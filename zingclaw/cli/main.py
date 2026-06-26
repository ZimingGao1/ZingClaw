"""CLI entrypoint for the current ZingClaw P0 runtime."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from zingclaw import __version__
from zingclaw.agent import AgentLoop
from zingclaw.bus import InboundMessage, MessageBus, OutboundMessage
from zingclaw.config import ensure_runtime_dirs, get_settings

app = typer.Typer(
    name="zingclaw",
    add_completion=False,
    no_args_is_help=True,
    help="ZingClaw - transparent local agent runtime",
)
console = Console()
_EXIT_COMMANDS = {"/exit", "/quit", "exit", "quit"}


@dataclass(slots=True)
class ChatTurnResult:
    final_content: str | None
    stop_reason: str = "completed"
    error: str | None = None


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
    console.print("Commands: `/help` `/exit` `/model` `/session` `/clear`")


def _result_from_outbound(msg: OutboundMessage) -> ChatTurnResult:
    metadata = dict(msg.metadata or {})
    error = metadata.get("error")
    return ChatTurnResult(
        final_content=None if metadata.get("_streamed") else (msg.text or None),
        stop_reason=str(metadata.get("stop_reason") or "completed"),
        error=error if isinstance(error, str) else None,
    )


async def _run_one_turn_async(loop: AgentLoop, *, session_key: str, message: str) -> tuple[ChatTurnResult, bool]:
    bus = MessageBus()
    loop.bind_bus(bus)
    loop_task = asyncio.create_task(loop.run())
    streamed = False

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
            outbound = await bus.consume_outbound()
            if outbound.channel != "cli" or outbound.session_key != session_key:
                continue
            if outbound.metadata.get("_stream_delta"):
                streamed = True
                sys.stdout.write(outbound.text)
                sys.stdout.flush()
                continue
            return _result_from_outbound(outbound), streamed
    finally:
        loop.stop()
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)


def _run_one_turn(loop: AgentLoop, *, session_key: str, message: str) -> Any:
    return asyncio.run(_run_one_turn_async(loop, session_key=session_key, message=message))


def _clear_session(loop: AgentLoop, session_key: str) -> None:
    session = loop.sessions.get_or_create(session_key)
    session.clear()
    loop.sessions.save(session)
    console.print(f"Cleared session `{session_key}`.")


def _interactive_chat(loop: AgentLoop, *, session_key: str) -> None:
    console.print(f"model: {loop.settings.model_name}")
    console.print(f"session: {session_key}")
    console.print(f"workspace: {loop.settings.workspace_dir}")
    _print_chat_help()

    while True:
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
        if message == "/help":
            _print_chat_help()
            continue
        if message == "/model":
            console.print(loop.settings.model_name)
            continue
        if message == "/session":
            console.print(session_key)
            continue
        if message == "/clear":
            _clear_session(loop, session_key)
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
    table.add_row("model", settings.model_name)
    table.add_row("workspace", str(settings.workspace_dir))
    table.add_row("sessions", str(settings.sessions_dir))
    table.add_row("memory", str(settings.memory_dir))
    table.add_row("logs", str(settings.logs_dir))
    table.add_row("api key", "configured" if settings.openai_api_key else "missing")
    table.add_row("api base", settings.openai_api_base or "(provider default)")
    table.add_row("max iterations", str(settings.max_iterations))
    table.add_row("max tool chars", str(settings.max_tool_result_chars))
    console.print(table)


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
        if message.strip():
            result, streamed = _run_one_turn(loop, session_key=session, message=message)
            _print_chat_result(result, already_streamed=streamed)
        else:
            _interactive_chat(loop, session_key=session)
    finally:
        loop.close()


def main() -> None:
    app()
