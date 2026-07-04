"""Rule-based trace curation into replay case candidates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from zingclaw.evaluation.dataset import enrich_replay_case_payload
from zingclaw.evaluation.replay import ReplayCase

Decision = Literal["regression", "candidate", "discard"]

_LOCAL_TOOLS = {
    "apply_patch",
    "edit_file",
    "exec",
    "find_files",
    "grep",
    "list_dir",
    "read_file",
    "write_file",
}
_EXTERNAL_TOOLS = {"web_fetch", "web_search"}
_STATEFUL_OR_AMBIGUOUS_TOOLS = {
    "complete_goal",
    "cron",
    "long_task",
    "message",
    "save_memory",
    "spawn",
}
_SAFETY_MARKERS = (
    "sensitive path",
    "outside the allowed workspace boundary",
    "outside the configured workspace",
    "blocked by safety guard",
    "blocked by deny pattern",
    "hard policy boundary",
)


@dataclass(slots=True)
class CuratedTrace:
    """One trace curation decision."""

    trace_id: str
    trace_path: str
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    case_path: str | None = None
    tools_used: list[str] = field(default_factory=list)
    stop_reason: str = ""
    score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "trace_path": self.trace_path,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "case_path": self.case_path,
            "tools_used": list(self.tools_used),
            "stop_reason": self.stop_reason,
            "score": self.score,
        }


@dataclass(slots=True)
class CurationResult:
    """Summary of one curation run."""

    trace_root: Path
    output_dir: Path
    report_path: Path
    items: list[CuratedTrace]

    @property
    def counts(self) -> dict[str, int]:
        counts = {"regression": 0, "candidate": 0, "discard": 0}
        for item in self.items:
            counts[item.decision] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "trace_root": str(self.trace_root),
            "output_dir": str(self.output_dir),
            "counts": self.counts,
            "items": [item.to_dict() for item in self.items],
        }


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return event
    runtime_event = payload.get("event")
    return runtime_event if isinstance(runtime_event, dict) else payload


def _tool_name(event: dict[str, Any]) -> str:
    return str(_payload(event).get("tool_name") or "")


def _tool_args(event: dict[str, Any]) -> dict[str, Any]:
    args = _payload(event).get("arguments")
    return args if isinstance(args, dict) else {}


def _tool_result_text(event: dict[str, Any]) -> str:
    payload = _payload(event)
    return str(payload.get("result_preview") or payload.get("content") or "")


def _contains_safety_marker(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _SAFETY_MARKERS)


def _normal_path(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")


def _first_user_input(events: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    for event in events:
        if event.get("type") != "user_input":
            continue
        payload = _payload(event)
        metadata = payload.get("metadata")
        return str(payload.get("input") or ""), metadata if isinstance(metadata, dict) else {}
    return "", {}


def _completion_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("type") == "turn_completed":
            return _payload(event)
    for event in reversed(events):
        if event.get("type") == "turn_result":
            return _payload(event)
    return {}


def _trace_id(trace_dir: Path, metadata: dict[str, Any]) -> str:
    raw = metadata.get("trace_id")
    return str(raw or trace_dir.name)


def _case_name(trace_id: str, metadata: dict[str, Any], input_metadata: dict[str, Any]) -> str:
    context = metadata.get("context")
    context_meta = context.get("metadata") if isinstance(context, dict) else {}
    if not isinstance(context_meta, dict):
        context_meta = {}
    for source in (input_metadata, context_meta):
        question_id = source.get("trace_question_id")
        if isinstance(question_id, str) and question_id.strip():
            return _safe_name(question_id)
    case = metadata.get("case")
    if isinstance(case, dict) and isinstance(case.get("name"), str) and case["name"].strip():
        return _safe_name(case["name"])
    return _safe_name(trace_id)


def _safe_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip()).strip("._-").lower()
    return name or "trace_case"


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _touched_files(tool_started: list[dict[str, Any]]) -> list[str]:
    touched: list[str] = []
    for event in tool_started:
        args = _tool_args(event)
        path = args.get("path")
        if isinstance(path, str) and path.strip():
            touched.append(_normal_path(path))
        edits = args.get("edits")
        if isinstance(edits, list):
            for item in edits:
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    touched.append(_normal_path(item["path"]))
    return _unique_preserving_order(touched)


def _command_substrings(tool_started: list[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    for event in tool_started:
        if _tool_name(event) != "exec":
            continue
        args = _tool_args(event)
        command = args.get("command") or args.get("cmd")
        if isinstance(command, str) and command.strip():
            commands.append(command.strip())
    return _unique_preserving_order(commands)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_events(trace_dir: Path) -> list[dict[str, Any]]:
    events_path = trace_dir / "trace.jsonl"
    if not events_path.exists():
        return []
    return [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _iter_trace_dirs(trace_root: Path) -> list[Path]:
    root = Path(trace_root).expanduser().resolve(strict=False)
    if root.is_file() and root.name == "trace.jsonl":
        return [root.parent]
    if (root / "trace.jsonl").exists():
        return [root]
    if not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir() and (path / "trace.jsonl").exists())


def _build_replay_case(
    *,
    name: str,
    split: Literal["regression", "candidate"],
    user_input: str,
    stop_reason: str,
    tools_used: list[str],
    expected_files_touched: list[str],
    expected_command_substrings: list[str],
    allow_tool_errors: bool,
    allow_safety_blocks: bool,
    source_trace_id: str,
) -> dict[str, Any]:
    case = ReplayCase(
        name=name,
        user_input=user_input,
        session_key=f"eval:{name}",
        channel="eval",
        expected_status=stop_reason or "completed",
        require_final_answer=True,
        expected_tools=tools_used,
        expected_command_substrings=expected_command_substrings,
        expected_files_touched=expected_files_touched,
        allow_tool_errors=allow_tool_errors,
        allow_safety_blocks=allow_safety_blocks,
    ).to_dict()
    case["source_trace_id"] = source_trace_id
    case["curation"] = {
        "generated_by": "zingclaw eval curate",
        "policy": "deterministic-local-trace-v1",
    }
    return enrich_replay_case_payload(case, split=split)


def _write_case(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _decision_for_trace(
    *,
    user_input: str,
    final_content: str,
    stop_reason: str,
    tools_used: list[str],
    tool_error_count: int,
    safety_block_count: int,
) -> tuple[Decision, int, list[str]]:
    reasons: list[str] = []
    score = 100

    if not user_input.strip():
        return "discard", 0, ["missing user_input"]
    if stop_reason != "completed":
        return "discard", 0, [f"stop_reason is {stop_reason or '(missing)'}"]
    if not final_content.strip():
        return "discard", 0, ["missing final answer"]
    if not tools_used:
        return "discard", 20, ["no tool trajectory to verify deterministically"]

    tool_set = set(tools_used)
    if tool_error_count:
        reasons.append(f"tool errors observed: {tool_error_count}")
        score -= 30
    if safety_block_count:
        reasons.append(f"safety blocks observed: {safety_block_count}")
        score -= 20
    if tool_set.intersection(_EXTERNAL_TOOLS):
        reasons.append("uses external/network tools")
        score -= 30
    if tool_set.intersection(_STATEFUL_OR_AMBIGUOUS_TOOLS):
        reasons.append("uses stateful or ambiguous tools")
        score -= 20
    if any(tool.startswith("mcp_") for tool in tool_set):
        reasons.append("uses MCP tools")
        score -= 20
    if len(tools_used) > 8:
        reasons.append("long multi-tool trajectory")
        score -= 10
    if not tool_set.intersection(_LOCAL_TOOLS):
        reasons.append("no stable local built-in tool assertions")
        score -= 30

    if reasons:
        return "candidate", max(0, score), reasons
    return "regression", score, ["stable local deterministic trace"]


def curate_traces(
    trace_root: Path,
    *,
    output_dir: Path,
    include_candidates: bool = True,
) -> CurationResult:
    """Curate trace directories into regression/candidate replay case files."""
    root = Path(trace_root).expanduser().resolve(strict=False)
    output = Path(output_dir).expanduser().resolve(strict=False)
    regression_dir = output / "regression"
    candidates_dir = output / "candidates"
    items: list[CuratedTrace] = []
    used_names: set[str] = set()

    for trace_dir in _iter_trace_dirs(root):
        events = _read_events(trace_dir)
        metadata = _read_json(trace_dir / "metadata.json")
        trace_id = _trace_id(trace_dir, metadata)
        user_input, input_metadata = _first_user_input(events)
        completed = _completion_payload(events)
        final_content = str(completed.get("final_content") or "")
        stop_reason = str(completed.get("stop_reason") or "")
        tool_started = [event for event in events if event.get("type") == "tool_started"]
        tool_completed = [event for event in events if event.get("type") == "tool_completed"]
        tools_used = _unique_preserving_order([_tool_name(event) for event in tool_started])
        tool_error_count = sum(1 for event in tool_completed if bool(_payload(event).get("is_error")))
        safety_block_count = sum(
            1 for event in tool_completed if _contains_safety_marker(_tool_result_text(event))
        )
        decision, score, reasons = _decision_for_trace(
            user_input=user_input,
            final_content=final_content,
            stop_reason=stop_reason,
            tools_used=tools_used,
            tool_error_count=tool_error_count,
            safety_block_count=safety_block_count,
        )
        case_path: Path | None = None
        if decision in {"regression", "candidate"} and (decision == "regression" or include_candidates):
            base_name = _case_name(trace_id, metadata, input_metadata)
            case_name = base_name
            if case_name in used_names:
                case_name = _safe_name(f"{base_name}_{trace_id}")
            used_names.add(case_name)
            case_payload = _build_replay_case(
                name=case_name,
                split=decision,
                user_input=user_input,
                stop_reason=stop_reason,
                tools_used=tools_used,
                expected_files_touched=_touched_files(tool_started),
                expected_command_substrings=_command_substrings(tool_started),
                allow_tool_errors=tool_error_count > 0,
                allow_safety_blocks=safety_block_count > 0,
                source_trace_id=trace_id,
            )
            case_path = (regression_dir if decision == "regression" else candidates_dir) / f"{case_name}.json"
            _write_case(case_path, case_payload)

        items.append(
            CuratedTrace(
                trace_id=trace_id,
                trace_path=str(trace_dir),
                decision=decision,
                reasons=reasons,
                case_path=str(case_path) if case_path is not None else None,
                tools_used=tools_used,
                stop_reason=stop_reason,
                score=score,
            )
        )

    result = CurationResult(
        trace_root=root,
        output_dir=output,
        report_path=output / "curation_report.json",
        items=items,
    )
    output.mkdir(parents=True, exist_ok=True)
    result.report_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
