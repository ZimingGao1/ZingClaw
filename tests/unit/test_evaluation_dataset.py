from __future__ import annotations

import json
from pathlib import Path

from zingclaw.evaluation import enrich_replay_case_payload, organize_replay_dataset


def test_enrich_replay_case_payload_infers_metadata() -> None:
    payload = {
        "name": "read_hello",
        "input": "read hello.py",
        "expected_tools": ["read_file"],
        "expected_files_touched": ["hello.py"],
    }

    enriched = enrich_replay_case_payload(payload, split="regression")

    assert enriched["category"] == "filesystem"
    assert enriched["stability"] == "stable"
    assert "regression" in enriched["tags"]
    assert "tool:read_file" in enriched["tags"]
    assert "read_only" in enriched["tags"]
    assert enriched["purpose"].startswith("Protect filesystem behavior")


def test_enrich_replay_case_payload_refreshes_existing_inferred_metadata() -> None:
    payload = {
        "name": "mcp_status_explain",
        "input": "explain mcp status",
        "category": "safety",
        "purpose": "old purpose",
        "stability": "old",
        "expected_tools": ["list_dir"],
        "allow_safety_blocks": True,
    }

    default = enrich_replay_case_payload(payload, split="candidate")
    refreshed = enrich_replay_case_payload(payload, split="candidate", refresh=True)

    assert default["category"] == "safety"
    assert default["purpose"] == "old purpose"
    assert refreshed["category"] == "mcp"
    assert refreshed["purpose"].startswith("Protect mcp behavior")
    assert refreshed["stability"] == "candidate"


def test_organize_replay_dataset_writes_manifest_and_normalizes_cases(tmp_path: Path) -> None:
    root = tmp_path / "replay_cases"
    regression = root / "regression"
    candidates = root / "candidates"
    regression.mkdir(parents=True)
    candidates.mkdir()
    (regression / "write_note.json").write_text(
        json.dumps(
            {
                "name": "write_note",
                "input": "write a note",
                "expected_tools": ["write_file"],
                "expected_files_written": ["note.txt"],
            }
        ),
        encoding="utf-8",
    )
    (regression / "semantic.json").write_text(
        json.dumps(
            {
                "name": "semantic",
                "input": "summarize workspace",
                "expected_answer_contains": ["workspace"],
            }
        ),
        encoding="utf-8",
    )
    (candidates / "weather.json").write_text(
        json.dumps(
            {
                "name": "weather",
                "input": "search weather",
                "expected_tools": ["web_search"],
            }
        ),
        encoding="utf-8",
    )
    (regression / "suite_report.json").write_text(json.dumps({"total": 1}), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"items": []}), encoding="utf-8")

    result = organize_replay_dataset(root, normalize=True)

    assert result.counts == {"regression": 2, "candidate": 1, "issues": 0}
    assert result.manifest_path == root / "manifest.json"
    assert result.manifest_path.exists()
    normalized = json.loads((regression / "write_note.json").read_text(encoding="utf-8"))
    assert normalized["category"] == "filesystem"
    assert normalized["stability"] == "stable"
    assert "writes_files" in normalized["tags"]
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["counts"]["regression"] == 2
    assert manifest["items"][2]["category"] == "web"
