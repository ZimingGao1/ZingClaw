from __future__ import annotations

from pathlib import Path

import pytest

from zingclaw.evaluation import (
    TaskContract,
    diff_snapshots,
    discover_task_contracts,
    prepare_task_workspace,
    snapshot_workspace,
)


def _contract(task_id: str = "fixture_task") -> TaskContract:
    return TaskContract.from_dict(
        {
            "id": task_id,
            "fixture": "basic_workspace",
            "input": "inspect fixture",
            "verifiers": [{"type": "status_completed"}],
        }
    )


def _make_fixture(root: Path) -> None:
    workspace = root / "basic_workspace" / "workspace"
    (workspace / "test").mkdir(parents=True)
    (workspace / "notes").mkdir()
    (workspace / "test" / "hello.py").write_text('print("hello")\n', encoding="utf-8")
    (workspace / "notes" / "trace_demo.md").write_text("trace demo\n", encoding="utf-8")


def test_prepare_task_workspace_copies_fixture_and_records_snapshot(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    runs = tmp_path / "runs"
    _make_fixture(fixtures)

    workspace = prepare_task_workspace(
        _contract("copy_fixture"),
        fixtures_root=fixtures,
        runs_root=runs,
        run_id="run-1",
    )

    assert workspace.workspace_dir == runs / "run-1" / "tasks" / "copy_fixture" / "workspace"
    assert (workspace.workspace_dir / "test" / "hello.py").read_text(encoding="utf-8") == 'print("hello")\n'
    assert "test/hello.py" in workspace.before.entries
    assert "notes" in workspace.before.entries


def test_workspace_diff_detects_created_modified_and_deleted_files(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    runs = tmp_path / "runs"
    _make_fixture(fixtures)
    workspace = prepare_task_workspace(
        _contract("diff_fixture"),
        fixtures_root=fixtures,
        runs_root=runs,
        run_id="run-1",
    )

    (workspace.workspace_dir / "test" / "hello.py").write_text('print("changed")\n', encoding="utf-8")
    (workspace.workspace_dir / "created.txt").write_text("new file\n", encoding="utf-8")
    (workspace.workspace_dir / "notes" / "trace_demo.md").unlink()

    diff = workspace.diff()

    assert diff.changed is True
    assert diff.created == ["created.txt"]
    assert diff.modified == ["test/hello.py"]
    assert diff.deleted == ["notes/trace_demo.md"]


def test_snapshot_diff_is_stable_for_unchanged_workspace(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    (root / "a").mkdir(parents=True)
    (root / "a" / "b.txt").write_text("same\n", encoding="utf-8")

    before = snapshot_workspace(root)
    after = snapshot_workspace(root)
    diff = diff_snapshots(before, after)

    assert diff.changed is False
    assert diff.created == []
    assert diff.modified == []
    assert diff.deleted == []
    assert diff.unchanged == ["a", "a/b.txt"]


def test_prepare_task_workspace_rejects_missing_or_existing_fixture(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    runs = tmp_path / "runs"

    with pytest.raises(FileNotFoundError):
        prepare_task_workspace(_contract("missing"), fixtures_root=fixtures, runs_root=runs, run_id="run-1")

    _make_fixture(fixtures)
    prepare_task_workspace(_contract("exists"), fixtures_root=fixtures, runs_root=runs, run_id="run-1")
    with pytest.raises(FileExistsError):
        prepare_task_workspace(_contract("exists"), fixtures_root=fixtures, runs_root=runs, run_id="run-1")


def test_repository_task_contracts_prepare_against_basic_fixture(tmp_path: Path) -> None:
    contracts = discover_task_contracts(Path("eval_tasks"))

    for contract in contracts:
        workspace = prepare_task_workspace(
            contract,
            fixtures_root=Path("eval_fixtures"),
            runs_root=tmp_path / "runs",
            run_id=f"run-{contract.id}",
        )
        assert workspace.workspace_dir.exists()
        assert (workspace.workspace_dir / "test" / "hello.py").exists()
