from __future__ import annotations

import json
from pathlib import Path

import pytest

from zingclaw.evaluation import (
    ArtifactSpec,
    TaskContract,
    VerifierSpec,
    discover_task_contracts,
    load_task_contract,
)


def test_task_contract_loads_structured_contract(tmp_path: Path) -> None:
    path = tmp_path / "task.json"
    path.write_text(
        json.dumps(
            {
                "id": "create_script",
                "category": "shell",
                "fixture": "basic_workspace",
                "input": "create and run a script",
                "allowed_tools": ["write_file", "exec", "exec"],
                "forbidden_tools": ["web_fetch"],
                "expected_artifacts": [
                    {
                        "path": "scripts/demo.py",
                        "contains": ["print"],
                    }
                ],
                "verifiers": [
                    {"type": "status_completed"},
                    {"type": "tool_used", "name": "write_file"},
                    {"type": "file_contains", "path": "scripts/demo.py", "contains": ["print"]},
                    {"type": "final_answer_contains", "contains": ["done"]},
                ],
                "timeout_seconds": 90,
            }
        ),
        encoding="utf-8",
    )

    contract = load_task_contract(path)

    assert contract.id == "create_script"
    assert contract.fixture == "basic_workspace"
    assert contract.allowed_tools == ["write_file", "exec"]
    assert contract.expected_artifacts[0] == ArtifactSpec(
        path="scripts/demo.py",
        exists=True,
        contains=["print"],
    )
    assert contract.verifiers[1] == VerifierSpec(type="tool_used", params={"name": "write_file"})
    assert contract.to_dict()["timeout_seconds"] == 90


def test_task_contract_rejects_unsafe_paths() -> None:
    with pytest.raises(ValueError, match="must not escape"):
        TaskContract.from_dict(
            {
                "id": "bad_path",
                "fixture": "basic_workspace",
                "input": "read a file",
                "expected_artifacts": ["../secret.txt"],
                "verifiers": [{"type": "status_completed"}],
            }
        )

    with pytest.raises(ValueError, match="must be relative"):
        VerifierSpec.from_dict({"type": "file_exists", "path": "C:/Users/me/.env"})


def test_task_contract_rejects_invalid_verifiers_and_tool_overlap() -> None:
    with pytest.raises(ValueError, match="unsupported verifier type"):
        VerifierSpec.from_dict({"type": "llm_judge", "prompt": "score it"})

    with pytest.raises(ValueError, match="requires name"):
        VerifierSpec.from_dict({"type": "tool_used"})

    with pytest.raises(ValueError, match="both allowed and forbidden"):
        TaskContract.from_dict(
            {
                "id": "overlap",
                "fixture": "basic_workspace",
                "input": "run something",
                "allowed_tools": ["exec"],
                "forbidden_tools": ["exec"],
                "verifiers": [{"type": "status_completed"}],
            }
        )


def test_discover_task_contracts_loads_directory(tmp_path: Path) -> None:
    root = tmp_path / "tasks"
    root.mkdir()
    for name in ("a", "b"):
        (root / f"{name}.json").write_text(
            json.dumps(
                {
                    "id": name,
                    "fixture": "basic_workspace",
                    "input": f"task {name}",
                    "verifiers": [{"type": "status_completed"}],
                }
            ),
            encoding="utf-8",
        )

    contracts = discover_task_contracts(root)

    assert [contract.id for contract in contracts] == ["a", "b"]


def test_repository_sample_task_contracts_are_valid() -> None:
    contracts = discover_task_contracts(Path("eval_tasks"))

    assert {contract.id for contract in contracts} >= {
        "read_hello_py",
        "create_and_run_script",
        "dangerous_delete",
    }
    assert all(contract.fixture == "basic_workspace" for contract in contracts)
