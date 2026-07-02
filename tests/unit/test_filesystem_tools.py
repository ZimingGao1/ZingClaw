from __future__ import annotations

import pytest

from zingclaw.tools import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool


@pytest.mark.asyncio
async def test_list_dir_lists_directory_contents_and_ignores_common_noise(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x", encoding="utf-8")

    tool = ListDirTool(workspace=tmp_path)
    result = await tool.execute()

    assert "README.md" in result
    assert "src/" in result
    assert ".git" not in result


@pytest.mark.asyncio
async def test_list_dir_recursive_returns_nested_paths(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("pass", encoding="utf-8")
    (tmp_path / "src" / "utils.py").write_text("pass", encoding="utf-8")

    tool = ListDirTool(workspace=tmp_path)
    result = await tool.execute(path="src", recursive=True)

    assert "main.py" in result
    assert "utils.py" in result


@pytest.mark.asyncio
async def test_read_file_returns_line_numbered_slice_and_continue_hint(tmp_path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("\n".join(f"line {i}" for i in range(1, 7)), encoding="utf-8")

    tool = ReadFileTool(workspace=tmp_path)
    result = await tool.execute(path="sample.txt", start_line=2, end_line=4)

    assert "2| line 2" in result
    assert "4| line 4" in result
    assert "1| line 1" not in result
    assert "Use start_line=5 to continue" in result


@pytest.mark.asyncio
async def test_read_file_rejects_non_utf8_content(tmp_path) -> None:
    sample = tmp_path / "binary.bin"
    sample.write_bytes(b"\xff\xfe\x00\x01")

    tool = ReadFileTool(workspace=tmp_path)
    result = await tool.execute(path="binary.bin")

    assert "not valid UTF-8 text" in result


@pytest.mark.asyncio
async def test_read_file_truncates_large_output_and_points_to_next_line(tmp_path) -> None:
    sample = tmp_path / "big.txt"
    sample.write_text("\n".join("x" * 200 for _ in range(200)), encoding="utf-8")

    tool = ReadFileTool(workspace=tmp_path, max_result_chars=1200)
    result = await tool.execute(path="big.txt")

    assert len(result) < 1500
    assert "output truncated at line" in result
    assert "Use start_line=" in result


@pytest.mark.asyncio
async def test_write_file_creates_parent_directories_and_persists_content(tmp_path) -> None:
    tool = WriteFileTool(workspace=tmp_path)

    result = await tool.execute(path="notes/today.txt", content="hello")

    assert "Successfully wrote" in result
    assert (tmp_path / "notes" / "today.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_read_and_write_block_paths_outside_workspace(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")

    reader = ReadFileTool(workspace=workspace)
    writer = WriteFileTool(workspace=workspace)

    read_result = await reader.execute(path=str(secret))
    write_result = await writer.execute(path=str(outside / "hijack.txt"), content="nope")

    assert "outside the allowed workspace boundary" in read_result
    assert "outside the allowed workspace boundary" in write_result
    assert not (outside / "hijack.txt").exists()


@pytest.mark.asyncio
async def test_write_file_can_opt_in_extra_write_directory(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writable = tmp_path / "writable"
    writable.mkdir()

    tool = WriteFileTool(
        workspace=workspace,
        extra_write_allowed_dirs=[writable],
    )
    result = await tool.execute(path=str(writable / "ok.txt"), content="allowed")

    assert "Successfully wrote" in result
    assert (writable / "ok.txt").read_text(encoding="utf-8") == "allowed"


@pytest.mark.asyncio
async def test_edit_file_replaces_exact_match(tmp_path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text("hello world", encoding="utf-8")

    tool = EditFileTool(workspace=tmp_path)
    result = await tool.execute(path="sample.py", old_text="world", new_text="earth")

    assert "Successfully edited" in result
    assert sample.read_text(encoding="utf-8") == "hello earth"


@pytest.mark.asyncio
async def test_edit_file_warns_when_old_text_is_ambiguous(tmp_path) -> None:
    sample = tmp_path / "dup.txt"
    sample.write_text("foo\nbar\nfoo\nbar\n", encoding="utf-8")

    tool = EditFileTool(workspace=tmp_path)
    result = await tool.execute(path="dup.txt", old_text="foo\nbar", new_text="baz")

    assert "appears 2 times" in result
    assert "replace_all=true" in result
    assert sample.read_text(encoding="utf-8") == "foo\nbar\nfoo\nbar\n"


@pytest.mark.asyncio
async def test_edit_file_can_replace_selected_occurrence_and_all_matches(tmp_path) -> None:
    sample = tmp_path / "multi.txt"
    sample.write_text("foo middle foo tail foo", encoding="utf-8")

    tool = EditFileTool(workspace=tmp_path)
    one_result = await tool.execute(
        path="multi.txt",
        old_text="foo",
        new_text="bar",
        occurrence=2,
    )
    all_result = await tool.execute(
        path="multi.txt",
        old_text="foo",
        new_text="baz",
        replace_all=True,
    )

    assert "Successfully edited" in one_result
    assert "Successfully edited" in all_result
    assert sample.read_text(encoding="utf-8") == "baz middle bar tail baz"


@pytest.mark.asyncio
async def test_edit_file_supports_create_file_semantics(tmp_path) -> None:
    tool = EditFileTool(workspace=tmp_path)

    result = await tool.execute(
        path="notes/new.txt",
        old_text="",
        new_text="created",
    )

    assert "Successfully created" in result
    assert (tmp_path / "notes" / "new.txt").read_text(encoding="utf-8") == "created"


@pytest.mark.asyncio
async def test_edit_file_blocks_paths_outside_workspace(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")

    tool = EditFileTool(workspace=workspace)
    result = await tool.execute(path=str(secret), old_text="top", new_text="nope")

    assert "outside the allowed workspace boundary" in result
    assert secret.read_text(encoding="utf-8") == "top secret"


@pytest.mark.asyncio
async def test_read_file_reports_missing_path_consistently(tmp_path) -> None:
    tool = ReadFileTool(workspace=tmp_path)

    result = await tool.execute(path="   ")

    assert result == "Error: path is required"
