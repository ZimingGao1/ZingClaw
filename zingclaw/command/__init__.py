"""Slash command routing helpers."""

from zingclaw.command.builtin import (
    BUILTIN_COMMAND_SPECS,
    build_help_text,
    format_mcp_status_text,
    register_builtin_commands,
)
from zingclaw.command.router import CommandContext, CommandRouter

__all__ = [
    "BUILTIN_COMMAND_SPECS",
    "CommandContext",
    "CommandRouter",
    "build_help_text",
    "format_mcp_status_text",
    "register_builtin_commands",
]
