from __future__ import annotations

import pytest

from zingclaw.tools import ApplyPatchTool


@pytest.mark.asyncio
async def test_apply_patch_replaces_exact_text(tmp_path) -> None:
    target = tmp_path / "calc.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    tool = ApplyPatchTool(workspace=tmp_path)

    result = await tool.execute(
        edits=[
            {
                "path": "calc.py",
                "action": "replace",
                "old_text": "    return a + b",
                "new_text": "    return a - b",
            }
        ]
    )

    assert "update calc.py" in result
    assert target.read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"


@pytest.mark.asyncio
async def test_apply_patch_can_add_new_file_and_append_existing_content(tmp_path) -> None:
    existing = tmp_path / "notes.txt"
    existing.write_text("alpha", encoding="utf-8")
    tool = ApplyPatchTool(workspace=tmp_path)

    created = await tool.execute(
        edits=[
            {
                "path": "config.py",
                "action": "add",
                "new_text": "DEBUG = True",
            }
        ]
    )
    appended = await tool.execute(
        edits=[
            {
                "path": "notes.txt",
                "action": "add",
                "new_text": "beta",
            }
        ]
    )

    assert "add config.py" in created
    assert "update notes.txt" in appended
    assert (tmp_path / "config.py").read_text(encoding="utf-8") == "DEBUG = True\n"
    assert existing.read_text(encoding="utf-8") == "alpha\nbeta\n"


@pytest.mark.asyncio
async def test_apply_patch_batches_multiple_files_and_supports_dry_run(tmp_path) -> None:
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("X = 1\n", encoding="utf-8")
    second.write_text("from a import X\nprint(X)\n", encoding="utf-8")
    tool = ApplyPatchTool(workspace=tmp_path)

    preview = await tool.execute(
        edits=[
            {
                "path": "a.py",
                "action": "replace",
                "old_text": "X = 1",
                "new_text": "Y = 1",
            },
            {
                "path": "created.txt",
                "action": "add",
                "new_text": "new",
            },
        ],
        dry_run=True,
    )
    result = await tool.execute(
        edits=[
            {
                "path": "a.py",
                "action": "replace",
                "old_text": "X = 1",
                "new_text": "Y = 1",
            },
            {
                "path": "b.py",
                "action": "replace",
                "old_text": "from a import X",
                "new_text": "from a import Y",
            },
        ]
    )

    assert "Patch dry-run succeeded" in preview
    assert not (tmp_path / "created.txt").exists()
    assert "update a.py" in result
    assert "update b.py" in result
    assert first.read_text(encoding="utf-8") == "Y = 1\n"
    assert second.read_text(encoding="utf-8") == "from a import Y\nprint(X)\n"


@pytest.mark.asyncio
async def test_apply_patch_rejects_ambiguous_or_invalid_edits(tmp_path) -> None:
    target = tmp_path / "repeated.txt"
    target.write_text("target\nmiddle\ntarget\n", encoding="utf-8")
    tool = ApplyPatchTool(workspace=tmp_path)

    ambiguous = await tool.execute(
        edits=[
            {
                "path": "repeated.txt",
                "action": "replace",
                "old_text": "target",
                "new_text": "changed",
            }
        ]
    )
    invalid = await tool.execute(edits=[{"path": "oops.txt", "new_text": "x"}])

    assert "old_text appears multiple times" in ambiguous
    assert "action required for edit: oops.txt" in invalid
    assert target.read_text(encoding="utf-8") == "target\nmiddle\ntarget\n"


@pytest.mark.asyncio
async def test_apply_patch_rolls_back_when_late_edit_fails(tmp_path) -> None:
    first = tmp_path / "first.txt"
    first.write_text("before\n", encoding="utf-8")
    tool = ApplyPatchTool(workspace=tmp_path)

    result = await tool.execute(
        edits=[
            {
                "path": "first.txt",
                "action": "replace",
                "old_text": "before",
                "new_text": "after",
            },
            {
                "path": "missing.txt",
                "action": "replace",
                "old_text": "remove me",
                "new_text": "removed",
            },
        ]
    )

    assert "file to update does not exist: missing.txt" in result
    assert first.read_text(encoding="utf-8") == "before\n"


@pytest.mark.asyncio
async def test_apply_patch_respects_workspace_boundary_and_explicit_extra_write_dirs(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    writable = tmp_path / "writable"
    workspace.mkdir()
    outside.mkdir()
    writable.mkdir()

    restricted = ApplyPatchTool(workspace=workspace, allowed_dir=workspace)
    allowed = ApplyPatchTool(
        workspace=workspace,
        allowed_dir=workspace,
        extra_write_allowed_dirs=[writable],
    )

    blocked = await restricted.execute(
        edits=[
            {
                "path": str(outside / "nope.txt"),
                "action": "add",
                "new_text": "blocked",
            }
        ]
    )
    permitted = await allowed.execute(
        edits=[
            {
                "path": str(writable / "ok.txt"),
                "action": "add",
                "new_text": "allowed",
            }
        ]
    )

    assert "outside the allowed workspace boundary" in blocked
    assert "Patch applied" in permitted
    assert not (outside / "nope.txt").exists()
    assert (writable / "ok.txt").read_text(encoding="utf-8") == "allowed\n"
