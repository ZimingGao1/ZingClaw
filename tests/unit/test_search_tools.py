from __future__ import annotations

from pathlib import Path

import pytest

from zingclaw.tools.search import FindFilesTool, GrepTool


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_find_files_filters_by_query_type_and_skips_ignored_dirs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write(workspace / "src" / "app.py", "print('app')\n")
    _write(workspace / "src" / "app_test.py", "print('test')\n")
    _write(workspace / "notes.md", "# notes\n")
    _write(workspace / "node_modules" / "ignored.js", "console.log('ignore')\n")

    tool = FindFilesTool(workspace=workspace, allowed_dir=workspace, restrict_to_workspace=True)

    result = await tool.execute(path=".", query="app", type="py")

    assert "src/app.py" in result
    assert "src/app_test.py" in result
    assert "notes.md" not in result
    assert "node_modules" not in result


@pytest.mark.asyncio
async def test_grep_default_mode_returns_matching_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write(workspace / "src" / "app.py", "TOKEN = 'abc'\nprint(TOKEN)\n")
    _write(workspace / "src" / "other.py", "print('hello')\n")
    _write(workspace / "README.md", "token mention\n")

    tool = GrepTool(workspace=workspace, allowed_dir=workspace, restrict_to_workspace=True)

    result = await tool.execute(pattern="TOKEN", path=".", type="py")
    lines = result.splitlines()

    assert "src/app.py" in lines
    assert "README.md" not in result
    assert "src/other.py" not in result


@pytest.mark.asyncio
async def test_grep_content_mode_returns_context_blocks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write(
        workspace / "docs" / "guide.md",
        "intro line\nneedle appears here\nclosing line\n",
    )

    tool = GrepTool(workspace=workspace, allowed_dir=workspace, restrict_to_workspace=True)

    result = await tool.execute(
        pattern="needle",
        path="docs",
        output_mode="content",
        fixed_strings=True,
        context_before=1,
        context_after=1,
    )

    assert "docs/guide.md:2" in result
    assert "  1| intro line" in result or " 1| intro line" in result
    assert "> 2| needle appears here" in result
    assert "  3| closing line" in result or " 3| closing line" in result


@pytest.mark.asyncio
async def test_grep_count_mode_aggregates_matches(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write(workspace / "src" / "alpha.py", "todo\nTODO\n")
    _write(workspace / "src" / "beta.py", "TODO\n")

    tool = GrepTool(workspace=workspace, allowed_dir=workspace, restrict_to_workspace=True)

    result = await tool.execute(
        pattern="todo",
        path="src",
        output_mode="count",
        case_insensitive=True,
        type="py",
    )

    assert "src/alpha.py: 2" in result
    assert "src/beta.py: 1" in result
    assert "(total matches: 3 in 2 files)" in result
