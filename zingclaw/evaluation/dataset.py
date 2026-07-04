"""Replay case dataset organization helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

DatasetSplit = Literal["regression", "candidate"]

_FILE_TOOLS = {
    "apply_patch",
    "edit_file",
    "find_files",
    "grep",
    "list_dir",
    "read_file",
    "write_file",
}
_WRITE_TOOLS = {"apply_patch", "edit_file", "write_file"}
_SHELL_TOOLS = {"exec"}
_WEB_TOOLS = {"web_fetch", "web_search"}
_STATEFUL_TOOLS = {"complete_goal", "cron", "long_task", "message", "save_memory", "spawn"}


@dataclass(slots=True)
class ReplayCaseDatasetItem:
    """One case entry in the replay dataset manifest."""

    name: str
    split: DatasetSplit
    path: str
    category: str
    tags: list[str] = field(default_factory=list)
    purpose: str = ""
    stability: str = ""
    expected_tools: list[str] = field(default_factory=list)
    source_trace_id: str = ""
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "split": self.split,
            "path": self.path,
            "category": self.category,
            "tags": list(self.tags),
            "purpose": self.purpose,
            "stability": self.stability,
            "expected_tools": list(self.expected_tools),
            "source_trace_id": self.source_trace_id,
            "issues": list(self.issues),
        }


@dataclass(slots=True)
class ReplayDatasetResult:
    """Summary of one replay dataset organization run."""

    root: Path
    manifest_path: Path
    normalized: bool
    refreshed: bool
    items: list[ReplayCaseDatasetItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counts = {"regression": 0, "candidate": 0, "issues": 0}
        for item in self.items:
            counts[item.split] += 1
            counts["issues"] += len(item.issues)
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "root": str(self.root),
            "normalized": self.normalized,
            "refreshed": self.refreshed,
            "counts": self.counts,
            "warnings": list(self.warnings),
            "items": [item.to_dict() for item in self.items],
        }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return [str(value)]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _input_text(payload: dict[str, Any]) -> str:
    return str(payload.get("input") or payload.get("user_input") or "")


def _expected_tools(payload: dict[str, Any]) -> list[str]:
    return _string_list(payload.get("expected_tools", payload.get("expectedTools", [])))


def _has_safety_expectation(payload: dict[str, Any]) -> bool:
    return payload.get("expected_safety_blocks", payload.get("expectedSafetyBlocks")) is not None or bool(
        payload.get("allow_safety_blocks", payload.get("allowSafetyBlocks", False))
    )


def _infer_category(payload: dict[str, Any]) -> str:
    existing = str(payload.get("category") or "").strip()
    if existing:
        return existing
    name = str(payload.get("name") or "").casefold()
    user_input = _input_text(payload).casefold()
    tools = set(_expected_tools(payload))
    if any(tool.startswith("mcp_") for tool in tools) or "mcp" in name:
        return "mcp"
    if tools & _WEB_TOOLS:
        return "web"
    if "cron" in tools or "cron" in name:
        return "cron"
    if "spawn" in tools or "subagent" in name:
        return "subagent"
    if "save_memory" in tools or "memory" in name:
        return "memory"
    if "orientation" in name or "context_sources" in name:
        return "context"
    if any(word in name + " " + user_input for word in ("blocked", "dangerous", "secret", "safety", "sensitive")):
        return "safety"
    if tools & _SHELL_TOOLS:
        return "shell"
    if tools & _FILE_TOOLS:
        return "filesystem"
    if tools & _STATEFUL_TOOLS:
        return "runtime"
    return "chat"


def _infer_tags(payload: dict[str, Any], *, split: DatasetSplit, category: str) -> list[str]:
    tags = _string_list(payload.get("tags", []))
    tools = set(_expected_tools(payload))
    tags.extend([split, category])
    if tools:
        tags.append("uses_tools")
    for tool in sorted(tools):
        tags.append(f"tool:{tool}")
    if tools & _WRITE_TOOLS or _string_list(payload.get("expected_files_written", [])):
        tags.append("writes_files")
    elif tools and not tools.intersection(_SHELL_TOOLS | _WEB_TOOLS | _STATEFUL_TOOLS):
        tags.append("read_only")
    if tools & _SHELL_TOOLS:
        tags.append("shell")
    if tools & _WEB_TOOLS:
        tags.append("network")
    if tools & _STATEFUL_TOOLS:
        tags.append("stateful")
    if _has_safety_expectation(payload):
        tags.append("safety")
    if _string_list(payload.get("expected_answer_contains", [])):
        tags.append("semantic_assertion")
    if _string_list(payload.get("expected_artifacts", [])):
        tags.append("artifact_assertion")
    return _unique(tags)


def _infer_purpose(payload: dict[str, Any], *, category: str) -> str:
    existing = str(payload.get("purpose") or "").strip()
    if existing:
        return existing
    text = _input_text(payload).strip().replace("\r\n", " ").replace("\n", " ")
    if len(text) > 120:
        text = text[:117].rstrip() + "..."
    return f"Protect {category} behavior for: {text}" if text else f"Protect {category} behavior."


def _infer_stability(payload: dict[str, Any], *, split: DatasetSplit) -> str:
    existing = str(payload.get("stability") or "").strip()
    if existing:
        return existing
    return "stable" if split == "regression" else "candidate"


def enrich_replay_case_payload(
    payload: dict[str, Any],
    *,
    split: DatasetSplit,
    refresh: bool = False,
) -> dict[str, Any]:
    """Return a copy of a replay case with missing dataset metadata filled in."""
    enriched = dict(payload)
    if refresh:
        enriched.pop("category", None)
        enriched.pop("purpose", None)
        enriched.pop("stability", None)
        enriched.pop("tags", None)
    category = _infer_category(enriched)
    if refresh or not str(enriched.get("category") or "").strip():
        enriched["category"] = category
    if refresh or not str(enriched.get("purpose") or "").strip():
        enriched["purpose"] = _infer_purpose(enriched, category=category)
    if refresh or not str(enriched.get("stability") or "").strip():
        enriched["stability"] = _infer_stability(enriched, split=split)
    enriched["tags"] = _infer_tags(enriched, split=split, category=category)
    return enriched


def _case_issues(payload: dict[str, Any], *, split: DatasetSplit, seen_names: set[str]) -> list[str]:
    issues: list[str] = []
    name = str(payload.get("name") or "").strip()
    if not name:
        issues.append("missing name")
    elif name in seen_names:
        issues.append(f"duplicate name: {name}")
    if not _input_text(payload).strip():
        issues.append("missing input")
    expected_tools = _expected_tools(payload)
    has_assertions_beyond_status = any(
        _string_list(payload.get(field, []))
        for field in (
            "expected_answer_contains",
            "expected_files_touched",
            "expected_files_written",
            "expected_artifacts",
            "expected_command_substrings",
        )
    )
    if split == "regression" and not expected_tools and not has_assertions_beyond_status:
        issues.append("regression case has no expected tools or semantic/artifact assertions")
    if split == "regression" and not has_assertions_beyond_status:
        issues.append("regression case has weak assertions beyond status/tool checks")
    return issues


def _read_case(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_case(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _iter_cases(root: Path) -> list[tuple[DatasetSplit, Path]]:
    pairs: list[tuple[DatasetSplit, Path]] = []
    for split in ("regression", "candidate"):
        directory = root / ("candidates" if split == "candidate" else "regression")
        if directory.exists():
            pairs.extend(
                (split, path)
                for path in sorted(directory.rglob("*.json"))
                if path.is_file() and not path.name.startswith("suite_report") and path.name != "manifest.json"
            )
    return pairs


def organize_replay_dataset(
    root: Path,
    *,
    manifest_path: Path | None = None,
    normalize: bool = False,
    refresh: bool = False,
) -> ReplayDatasetResult:
    """Build a replay dataset manifest and optionally normalize case metadata."""
    dataset_root = Path(root).expanduser().resolve(strict=False)
    destination = (
        Path(manifest_path).expanduser().resolve(strict=False)
        if manifest_path is not None
        else dataset_root / "manifest.json"
    )
    items: list[ReplayCaseDatasetItem] = []
    warnings: list[str] = []
    seen_names: set[str] = set()

    if not dataset_root.exists():
        raise FileNotFoundError(f"replay dataset does not exist: {dataset_root}")

    for split, path in _iter_cases(dataset_root):
        try:
            original = _read_case(path)
        except Exception as exc:
            warnings.append(f"{path}: failed to read JSON ({type(exc).__name__}: {exc})")
            continue
        enriched = enrich_replay_case_payload(original, split=split, refresh=refresh)
        issues = _case_issues(enriched, split=split, seen_names=seen_names)
        name = str(enriched.get("name") or path.stem)
        if name:
            seen_names.add(name)
        if normalize and enriched != original:
            _write_case(path, enriched)
        items.append(
            ReplayCaseDatasetItem(
                name=name,
                split=split,
                path=str(path),
                category=str(enriched.get("category") or ""),
                tags=_string_list(enriched.get("tags", [])),
                purpose=str(enriched.get("purpose") or ""),
                stability=str(enriched.get("stability") or ""),
                expected_tools=_expected_tools(enriched),
                source_trace_id=str(enriched.get("source_trace_id") or ""),
                issues=issues,
            )
        )

    result = ReplayDatasetResult(
        root=dataset_root,
        manifest_path=destination,
        normalized=normalize,
        refreshed=refresh,
        items=items,
        warnings=warnings,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
