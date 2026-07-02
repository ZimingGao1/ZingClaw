from __future__ import annotations

from pathlib import Path

from zingclaw.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader


def _write_skill(base: Path, name: str, content: str) -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


def test_skills_loader_prefers_workspace_then_configured_then_builtin(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    configured = tmp_path / "configured-skills"
    builtin = tmp_path / "builtin-skills"
    workspace.mkdir()
    configured.mkdir()
    builtin.mkdir()

    _write_skill(
        workspace / "skills",
        "shared",
        "---\ndescription: workspace version\n---\n\n# Shared\n\nWorkspace copy.",
    )
    _write_skill(
        configured,
        "shared",
        "---\ndescription: configured version\n---\n\n# Shared\n\nConfigured copy.",
    )
    _write_skill(
        builtin,
        "memory",
        "---\ndescription: builtin memory\nalways: true\n---\n\n# Memory\n\nBuiltin memory skill.",
    )

    loader = SkillsLoader(workspace, skills_dir=configured, builtin_skills_dir=builtin)
    skills = loader.list_skills(filter_unavailable=False)

    assert [skill["name"] for skill in skills] == ["shared", "memory"]
    assert skills[0]["source"] == "workspace"
    assert skills[0]["path"].endswith("shared\\SKILL.md")
    assert loader.get_skill_metadata("shared") == {"description": "workspace version"}
    assert loader.get_always_skills() == ["memory"]


def test_skills_loader_prefers_configured_before_builtin(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    configured = tmp_path / "configured-skills"
    builtin = tmp_path / "builtin-skills"
    workspace.mkdir()
    configured.mkdir()
    builtin.mkdir()

    _write_skill(
        configured,
        "design-thinking",
        "---\ndescription: configured design thinking\n---\n\n# Design Thinking\n\nConfigured copy.",
    )
    _write_skill(
        builtin,
        "design-thinking",
        "---\ndescription: builtin design thinking\n---\n\n# Design Thinking\n\nBuiltin copy.",
    )

    loader = SkillsLoader(workspace, skills_dir=configured, builtin_skills_dir=builtin)
    skills = loader.list_skills(filter_unavailable=False)
    summary = loader.build_skills_summary()

    assert skills == [
        {
            "name": "design-thinking",
            "path": str(configured / "design-thinking" / "SKILL.md"),
            "source": "configured",
        }
    ]
    assert "configured design thinking" in summary
    assert "[configured]" in summary


def test_skills_loader_builds_summary_marks_unavailable_and_strips_frontmatter(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    configured = tmp_path / "configured-skills"
    workspace.mkdir()
    configured.mkdir()

    _write_skill(
        configured,
        "zoom",
        "# Zoom\n\nUse this skill for meeting links.",
    )
    _write_skill(
        configured,
        "github",
        (
            "---\n"
            'description: Interact with GitHub using the "gh" CLI.\n'
            'metadata: {"nanobot":{"requires":{"bins":["__missing_gh__"]}}}\n'
            "---\n\n"
            "# GitHub\n\nUse gh for issues and PRs."
        ),
    )

    loader = SkillsLoader(workspace, skills_dir=configured, builtin_skills_dir=tmp_path / "builtin")
    summary = loader.build_skills_summary()
    context_block = loader.load_skills_for_context(["zoom"])
    available, reason = loader.get_skill_availability("github")
    requirements = loader.get_skill_requirements("github")

    assert "- **zoom** - Use this skill for meeting links." in summary
    assert "github" in summary
    assert "unavailable: CLI: __missing_gh__" in summary
    assert "SKILL.md" not in summary
    assert context_block.startswith("### Skill: zoom")
    assert "Use this skill for meeting links." in context_block
    assert "---" not in context_block
    assert available is False
    assert reason == "CLI: __missing_gh__"
    assert requirements["missing_bins"] == ["__missing_gh__"]
    assert requirements["missing_env"] == []


def test_skills_loader_respects_disabled_skills_case_insensitively(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    configured = tmp_path / "configured-skills"
    builtin = tmp_path / "builtin-skills"
    workspace.mkdir()
    configured.mkdir()
    builtin.mkdir()

    _write_skill(
        configured,
        "zoom",
        "---\ndescription: configured zoom\n---\n\n# Zoom\n\nConfigured zoom skill.",
    )
    _write_skill(
        builtin,
        "memory",
        "---\ndescription: builtin memory\nalways: true\n---\n\n# Memory\n\nBuiltin memory skill.",
    )

    loader = SkillsLoader(
        workspace,
        skills_dir=configured,
        builtin_skills_dir=builtin,
        disabled_skills={"MEMORY", "zoom"},
    )

    skills = loader.list_skills(filter_unavailable=False)

    assert skills == []
    assert loader.get_always_skills() == []


def test_skills_loader_respects_enabled_skills_whitelist_case_insensitively(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    configured = tmp_path / "configured-skills"
    builtin = tmp_path / "builtin-skills"
    workspace.mkdir()
    configured.mkdir()
    builtin.mkdir()

    _write_skill(
        configured,
        "zoom",
        "---\ndescription: configured zoom\n---\n\n# Zoom\n\nConfigured zoom skill.",
    )
    _write_skill(
        builtin,
        "memory",
        "---\ndescription: builtin memory\nalways: true\n---\n\n# Memory\n\nBuiltin memory skill.",
    )

    loader = SkillsLoader(
        workspace,
        skills_dir=configured,
        builtin_skills_dir=builtin,
        enabled_skills={"MEMORY"},
    )

    skills = loader.list_skills(filter_unavailable=False)

    assert skills == [
        {
            "name": "memory",
            "path": str(builtin / "memory" / "SKILL.md"),
            "source": "builtin",
        }
    ]
    assert loader.get_always_skills() == ["memory"]
    assert loader.get_skill_availability("zoom") == (False, "skill not found or disabled")


def test_skills_loader_understands_nested_nanobot_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    builtin = tmp_path / "builtin-skills"
    workspace.mkdir()
    builtin.mkdir()

    _write_skill(
        builtin,
        "github",
        (
            "---\n"
            'metadata: {"nanobot":{"description":"Use gh for GitHub work.","requires":{"bins":["__missing_gh__"]}}}\n'
            "---\n\n"
            "# GitHub\n\nUse gh for issues and PRs."
        ),
    )

    loader = SkillsLoader(workspace, builtin_skills_dir=builtin)

    skills = loader.list_skills(filter_unavailable=False)
    available, reason = loader.get_skill_availability("github")

    assert skills == [
        {
            "name": "github",
            "path": str(builtin / "github" / "SKILL.md"),
            "source": "builtin",
        }
    ]
    assert "Use gh for GitHub work." in loader.build_skills_summary()
    assert "SKILL.md" not in loader.build_skills_summary()
    assert available is False
    assert reason == "CLI: __missing_gh__"


def test_repo_builtin_skills_include_memory_github_weather_and_long_goal(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    loader = SkillsLoader(workspace, builtin_skills_dir=BUILTIN_SKILLS_DIR)

    skills = loader.list_skills(filter_unavailable=False)
    names = [entry["name"] for entry in skills]
    summary = loader.build_skills_summary()

    assert "memory" in names
    assert "github" in names
    assert "cron" in names
    assert "long-goal" in names
    assert "weather" in names
    assert loader.get_always_skills() == ["memory"]
    assert "### Skill: memory" in loader.load_skills_for_context(["memory"])
    assert "- **cron** -" in summary
    assert "- **github** -" in summary
    assert "- **long-goal** -" in summary
    assert "- **weather** -" in summary
