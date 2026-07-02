"""Built-in slash commands for the local chat shell."""

from __future__ import annotations

from dataclasses import dataclass

from zingclaw.command.router import CommandContext, CommandRouter


@dataclass(frozen=True, slots=True)
class BuiltinCommandSpec:
    command: str
    description: str


BUILTIN_COMMAND_SPECS: tuple[BuiltinCommandSpec, ...] = (
    BuiltinCommandSpec("/help", "Show available chat commands."),
    BuiltinCommandSpec("/model", "Show the active model."),
    BuiltinCommandSpec("/session", "Show the current session key."),
    BuiltinCommandSpec("/clear", "Clear the current session history."),
    BuiltinCommandSpec("/mcp", "Show MCP runtime status."),
    BuiltinCommandSpec("/mcp reload", "Reload MCP config and reconnect servers."),
    BuiltinCommandSpec("/mcp reconnect", "Reconnect MCP servers without reloading config."),
)


def build_help_text(*, include_exit: bool = False) -> str:
    """Build canonical help text shared by chat entrypoints."""

    commands = [spec.command for spec in BUILTIN_COMMAND_SPECS]
    if include_exit:
        commands.insert(1, "/exit")
    return "Commands: " + " ".join(f"`{command}`" for command in commands)


def format_mcp_status_text(statuses: list[object]) -> str:
    """Render MCP server status as plain text for chat channels."""

    if not statuses:
        return "MCP Status\n- (none configured)"

    lines = ["MCP Status"]
    for item in statuses:
        state = "connected" if getattr(item, "connected", False) else "unavailable"
        lines.append(
            "- "
            + f"{getattr(item, 'name', '(unknown)')}: "
            + f"{getattr(item, 'transport', '-')}, "
            + f"{state}, "
            + f"prefix={getattr(item, 'tool_prefix', '-')}, "
            + "capabilities="
            + f"{getattr(item, 'capability_count', 0)} "
            + f"({getattr(item, 'tool_count', 0)}/{getattr(item, 'resource_count', 0)}/{getattr(item, 'prompt_count', 0)})"
        )
        error = getattr(item, "error", None)
        if error:
            lines.append(f"  error: {error}")
        tool_names = list(getattr(item, "tool_names", []) or [])
        resource_names = list(getattr(item, "resource_names", []) or [])
        prompt_names = list(getattr(item, "prompt_names", []) or [])
        if tool_names:
            lines.append("  tools: " + ", ".join(tool_names))
        if resource_names:
            lines.append("  resources: " + ", ".join(resource_names))
        if prompt_names:
            lines.append("  prompts: " + ", ".join(prompt_names))
    return "\n".join(lines)


async def cmd_help(ctx: CommandContext):
    return ctx.reply(build_help_text(include_exit=bool(ctx.metadata.get("include_exit"))))


async def cmd_model(ctx: CommandContext):
    return ctx.reply(str(ctx.loop.settings.model_name))


async def cmd_session(ctx: CommandContext):
    return ctx.reply(ctx.session_key)


async def cmd_clear(ctx: CommandContext):
    session = ctx.session or ctx.loop.sessions.get_or_create(ctx.session_key)
    session.clear()
    ctx.loop.sessions.save(session)
    return ctx.reply(f"Cleared session `{ctx.session_key}`.")


async def cmd_mcp_status(ctx: CommandContext):
    statuses = await ctx.loop.get_mcp_status()
    return ctx.reply(format_mcp_status_text(statuses))


async def cmd_mcp_reload(ctx: CommandContext):
    result = await ctx.loop.reload_mcp()
    statuses = await ctx.loop.get_mcp_status(connect_missing=False)
    text = str(result.get("message") or "MCP reload finished.")
    return ctx.reply(text + "\n\n" + format_mcp_status_text(statuses))


async def cmd_mcp_reconnect(ctx: CommandContext):
    server_name = (ctx.args or "").strip() or None
    result = await ctx.loop.reconnect_mcp(server_name)
    statuses = await ctx.loop.get_mcp_status(connect_missing=False)
    text = str(result.get("message") or "MCP reconnect finished.")
    return ctx.reply(text + "\n\n" + format_mcp_status_text(statuses))


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default chat slash commands."""

    router.exact("/help", cmd_help)
    router.exact("/model", cmd_model)
    router.exact("/session", cmd_session)
    router.exact("/clear", cmd_clear)
    router.exact("/mcp", cmd_mcp_status)
    router.exact("/mcp reload", cmd_mcp_reload)
    router.prefix("/mcp reconnect", cmd_mcp_reconnect)
