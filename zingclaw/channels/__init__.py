"""Channel abstractions for ZingClaw."""

from zingclaw.channels.base import BaseChannel, Channel
from zingclaw.channels.manager import ChannelManager

__all__ = [
    "BaseChannel",
    "Channel",
    "ChannelManager",
]
