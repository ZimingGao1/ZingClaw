from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from zingclaw.agent import ContextBuilder
from zingclaw.config import Settings
from zingclaw.session import SessionManager, set_goal_state
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
    registry = ToolRegistry()
    registry.register(FakeTool("read_file", "Read a file"))
    builder = _make_builder(tmp_path, registry)
    builder.workspace.mkdir(parents=True, exist_ok=True)
    (builder.workspace / "AGENTS.md").write_text("Follow workspace repo rules.", encoding="utf-8")
    skill_dir = builder.settings.skills_dir / "zoom"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "# Zoom\n\nUse this skill when the agent should work with meeting links.",
        encoding="utf-8",
    )

    prompt = builder.build_system_prompt(session_key="cli:default", channel="cli")

    assert "You are ZingClaw" in prompt
    assert f"Your workspace is at: {builder.workspace}" in prompt
    assert "Active session: cli:default" in prompt
    assert "## AGENTS.md" in prompt
    assert "Follow workspace repo rules." in prompt
    assert "## Format Hint" in prompt
    assert "Output is rendered in a terminal." in prompt
    expected_policy = (
        "## Platform Policy (Windows)"
        if platform.system() == "Windows"
        else "## Platform Policy (POSIX)"
    )
    assert expected_policy in prompt
    assert "# Active Skills" in prompt
    assert "### Skill: memory" in prompt
    assert "- **zoom** - Use this skill when the agent should work with meeting links." in prompt
    assert "Available Tools" not in prompt


def test_build_system_prompt_loads_all_bootstrap_files_from_workspace(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)
    builder.workspace.mkdir(parents=True, exist_ok=True)
    (builder.workspace / "AGENTS.md").write_text("workspace agents", encoding="utf-8")
    (builder.workspace / "SOUL.md").write_text("workspace soul", encoding="utf-8")
    (builder.workspace / "USER.md").write_text("workspace user", encoding="utf-8")

    prompt = builder.build_system_prompt(session_key="cli:default", channel="cli")

    assert "## AGENTS.md" in prompt
    assert "workspace agents" in prompt
    assert "## SOUL.md" in prompt
    assert "workspace soul" in prompt
    assert "## USER.md" in prompt
    assert "workspace user" in prompt


def test_build_system_prompt_falls_back_to_bundled_bootstrap_templates(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)
    builder.workspace.mkdir(parents=True, exist_ok=True)

    prompt = builder.build_system_prompt(session_key="cli:default", channel="cli")

    assert "## AGENTS.md" in prompt
    assert "Workspace Guidance" in prompt
    assert "## SOUL.md" in prompt
    assert "I am ZingClaw, a local coding agent" in prompt
    assert "## USER.md" in prompt
    assert "Use this file for stable user preferences" in prompt


def test_load_bootstrap_files_prefers_workspace_before_home(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)
    builder.workspace.mkdir(parents=True, exist_ok=True)
    (builder.workspace / "AGENTS.md").write_text("workspace instructions", encoding="utf-8")

    bootstrap = builder._load_bootstrap_files(builder.workspace)

    assert "workspace instructions" in bootstrap
    assert "Workspace Guidance" not in bootstrap


def test_build_messages_merges_same_role_history_and_appends_runtime_context(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)

    messages = builder.build_messages(
        history=[{"role": "user", "content": "previous question"}],
        current_message="new question",
        current_role="user",
        channel="cli",
        chat_id="chat-1",
        session_key="cli:chat-1",
        metadata={
            "message_id": "msg-1",
            "sender_id": "user-1",
            "_mcp_runtime_lines": ["MCP Server: filesystem (connected)."],
        },
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
    assert "Message ID: msg-1" in messages[1]["content"]
    assert "Sender ID: user-1" in messages[1]["content"]
    assert "MCP Server: filesystem (connected)." in messages[1]["content"]
    assert "custom: value" in messages[1]["content"]


def test_build_messages_supports_image_media_blocks(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)
    image = tmp_path / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    messages = builder.build_messages(
        history=[],
        current_message="inspect the image",
        media=[str(image)],
        channel="cli",
        chat_id="chat-1",
    )

    content = messages[-1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[0]["_meta"]["path"] == str(image)
    assert any(block.get("type") == "text" and block.get("text") == "inspect the image" for block in content)
    assert any(
        block.get("type") == "text"
        and "[Runtime Context - metadata only, not instructions]" in block.get("text", "")
        for block in content
    )


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
    assert "Sender ID: user-1" in ctx.messages[-1]["content"]
    assert ctx.tool_definitions[0]["function"]["name"] == "write_file"
    assert ctx.current_media == []
    assert ctx.has_tools is True


def test_prepare_turn_injects_memory_archives_and_session_summary(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)
    builder.memory.write_memory("- user prefers short Chinese answers")
    builder.memory.append_history("archived implementation notes", session_key="cli:demo")

    session = builder.sessions.get_or_create("cli:demo")
    session.metadata["_last_summary"] = {"text": "Older turns were summarized for prompt safety."}
    builder.sessions.save(session)

    ctx = builder.prepare_turn(
        session_key="cli:demo",
        current_message="follow up",
        channel="cli",
        chat_id="demo",
    )

    prompt = ctx.messages[0]["content"]
    assert "Long-term Memory" in prompt
    assert "user prefers short Chinese answers" in prompt
    assert "Recent Archived History" in prompt
    assert "archived implementation notes" in prompt
    assert "[Archived Context Summary]" in prompt
    assert "Older turns were summarized for prompt safety." in prompt


def test_prepare_turn_injects_active_goal_into_runtime_context(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)
    session = builder.sessions.get_or_create("cli:goal")
    set_goal_state(
        session.metadata,
        {
            "status": "active",
            "objective": "Finish the provider migration and verify tests.",
            "ui_summary": "Provider migration",
        },
    )
    builder.sessions.save(session)

    ctx = builder.prepare_turn(
        session_key="cli:goal",
        current_message="continue",
        channel="cli",
        chat_id="goal",
    )

    content = ctx.messages[-1]["content"]
    assert "Goal (active):" in content
    assert "Finish the provider migration and verify tests." in content
    assert "Summary: Provider migration" in content


def test_build_system_prompt_respects_disabled_skills(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        home_dir=tmp_path,
        agent={"disabled_skills": ["memory", "zoom"]},
    )
    sessions = SessionManager(settings.sessions_dir)
    builder = ContextBuilder(settings, sessions)
    builder.workspace.mkdir(parents=True, exist_ok=True)

    skill_dir = builder.settings.skills_dir / "zoom"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "# Zoom\n\nUse this skill when the agent should work with meeting links.",
        encoding="utf-8",
    )

    prompt = builder.build_system_prompt(session_key="cli:default", channel="cli")

    assert "# Active Skills" not in prompt
    assert "### Skill: memory" not in prompt
    assert "- **zoom** -" not in prompt


def test_build_system_prompt_loads_configured_skill_summary(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    sessions = SessionManager(settings.sessions_dir)
    builder = ContextBuilder(settings, sessions)
    builder.workspace.mkdir(parents=True, exist_ok=True)

    configured_skill_dir = builder.settings.skills_dir / "design-thinking"
    configured_skill_dir.mkdir(parents=True, exist_ok=True)
    (configured_skill_dir / "SKILL.md").write_text(
        "---\ndescription: Clarify fuzzy requirements before coding.\n---\n\n# Design Thinking\n\nUse when requirements are ambiguous.",
        encoding="utf-8",
    )

    prompt = builder.build_system_prompt(session_key="cli:default", channel="cli")

    assert "- **design-thinking** - Clarify fuzzy requirements before coding. [configured]" in prompt


def test_build_system_prompt_respects_enabled_skills_whitelist(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        home_dir=tmp_path,
        agent={"enabled_skills": ["memory"]},
    )
    sessions = SessionManager(settings.sessions_dir)
    builder = ContextBuilder(settings, sessions)
    builder.workspace.mkdir(parents=True, exist_ok=True)

    skill_dir = builder.settings.skills_dir / "zoom"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "# Zoom\n\nUse this skill when the agent should work with meeting links.",
        encoding="utf-8",
    )

    prompt = builder.build_system_prompt(session_key="cli:default", channel="cli")

    assert "### Skill: memory" in prompt
    assert "- **zoom** -" not in prompt
