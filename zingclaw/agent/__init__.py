"""Agent runtime modules with lazy exports to avoid import cycles."""

from __future__ import annotations

from typing import Any

__all__ = [
    "AgentContext",
    "AgentLoop",
    "AgentLoopResult",
    "AgentRunResult",
    "AgentRunSpec",
    "AgentRunner",
    "ContextBuilder",
    "SkillsLoader",
    "SubagentManager",
    "SubagentStatus",
]


def __getattr__(name: str) -> Any:
    if name in {"AgentContext", "ContextBuilder"}:
        from zingclaw.agent.context import AgentContext, ContextBuilder

        return {"AgentContext": AgentContext, "ContextBuilder": ContextBuilder}[name]
    if name in {"AgentLoop", "AgentLoopResult"}:
        from zingclaw.agent.loop import AgentLoop, AgentLoopResult

        return {"AgentLoop": AgentLoop, "AgentLoopResult": AgentLoopResult}[name]
    if name in {"AgentRunResult", "AgentRunSpec", "AgentRunner"}:
        from zingclaw.agent.runner import AgentRunResult, AgentRunSpec, AgentRunner

        return {
            "AgentRunResult": AgentRunResult,
            "AgentRunSpec": AgentRunSpec,
            "AgentRunner": AgentRunner,
        }[name]
    if name == "SkillsLoader":
        from zingclaw.agent.skills import SkillsLoader

        return SkillsLoader
    if name in {"SubagentManager", "SubagentStatus"}:
        from zingclaw.agent.subagent import SubagentManager, SubagentStatus

        return {"SubagentManager": SubagentManager, "SubagentStatus": SubagentStatus}[name]
    raise AttributeError(name)
