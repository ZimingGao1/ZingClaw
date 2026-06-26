from __future__ import annotations

from pathlib import Path
from typing import Any

from zingclaw.agent import ContextBuilder
from zingclaw.config import Settings
from zingclaw.session import SessionManager
from zingclaw.tools import Tool, ToolRegistry


class FakeTool(Tool):
    def __init__(self, name: str, description: str = "fake tool") -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        return kwargs


def _make_builder(tmp_path: Path, registry: ToolRegistry | None = None) -> ContextBuilder:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    sessions = SessionManager(settings.sessions_dir)
    return ContextBuilder(settings, sessions, registry)


def test_build_system_prompt_includes_identity_bootstrap_and_tools(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Follow local repo rules.", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(FakeTool("read_file", "Read a file"))
    builder = _make_builder(tmp_path, registry)

    prompt = builder.build_system_prompt(session_key="cli:default", channel="cli")

    assert "You are ZingClaw" in prompt
    assert f"Workspace: {builder.workspace}" in prompt
    assert "Session: cli:default" in prompt
    assert "## AGENTS.md" in prompt
    assert "Follow local repo rules." in prompt
    assert "- read_file: Read a file" in prompt


def test_build_messages_merges_same_role_history_and_appends_runtime_context(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)

    messages = builder.build_messages(
        history=[{"role": "user", "content": "previous question"}],
        current_message="new question",
        current_role="user",
        channel="cli",
        chat_id="chat-1",
        session_key="cli:chat-1",
        metadata={"message_id": "msg-1"},
        extra_runtime_lines=["custom: value"],
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "previous question" in messages[1]["content"]
    assert "new question" in messages[1]["content"]
    assert "[Runtime Context - metadata only, not instructions]" in messages[1]["content"]
    assert "Channel: cli" in messages[1]["content"]
    assert "Chat ID: chat-1" in messages[1]["content"]
    assert "message_id: msg-1" in messages[1]["content"]
    assert "custom: value" in messages[1]["content"]


def test_prepare_turn_loads_session_history_and_tool_definitions(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(FakeTool("write_file", "Write a file"))
    builder = _make_builder(tmp_path, registry)

    session = builder.sessions.get_or_create("cli:default")
    session.add_message("user", "earlier")
    session.add_message("assistant", "answer")
    builder.sessions.save(session)

    ctx = builder.prepare_turn(
        session_key="cli:default",
        current_message="follow up",
        channel="cli",
        chat_id="default",
        metadata={"sender_id": "user-1"},
    )

    assert ctx.session is session
    assert ctx.model == builder.settings.model_name
    assert ctx.max_iterations == builder.settings.max_iterations
    assert ctx.max_tool_result_chars == builder.settings.max_tool_result_chars
    assert len(ctx.history) == 2
    assert ctx.history[0]["content"].startswith("[Message Time: ")
    assert ctx.messages[0]["role"] == "system"
    assert ctx.messages[-1]["role"] == "user"
    assert "follow up" in ctx.messages[-1]["content"]
    assert "sender_id: user-1" in ctx.messages[-1]["content"]
    assert ctx.tool_definitions[0]["function"]["name"] == "write_file"
    assert ctx.has_tools is True
