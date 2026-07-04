"""Replay case models and runner integration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zingclaw.evaluation.scorecard import Scorecard, evaluate_trace
from zingclaw.evaluation.trace import TraceStore


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return [str(value)]


@dataclass(slots=True)
class ReplayCase:
    """A deterministic rule-based replay target for one agent task."""

    name: str
    user_input: str
    session_key: str = "eval:default"
    channel: str = "eval"
    chat_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    expected_status: str = "completed"
    require_final_answer: bool = True
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    allowed_extra_tools: list[str] = field(default_factory=list)
    expected_command_substrings: list[str] = field(default_factory=list)
    expected_files_touched: list[str] = field(default_factory=list)
    expected_files_written: list[str] = field(default_factory=list)
    expected_artifacts: list[str] = field(default_factory=list)
    expected_answer_contains: list[str] = field(default_factory=list)
    expected_safety_blocks: int | None = None
    allow_tool_errors: bool = True
    allow_safety_blocks: bool = False
    disallow_unexpected_dangerous_tools: bool = True
    category: str = ""
    tags: list[str] = field(default_factory=list)
    purpose: str = ""
    stability: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReplayCase":
        user_input = payload.get("input", payload.get("user_input", ""))
        if not str(user_input).strip():
            raise ValueError("replay case requires non-empty input")
        name = str(payload.get("name") or "replay_case").strip()
        expected_safety_blocks = payload.get("expected_safety_blocks", payload.get("expectedSafetyBlocks"))
        if isinstance(expected_safety_blocks, bool):
            parsed_expected_safety_blocks: int | None = 1 if expected_safety_blocks else 0
        elif expected_safety_blocks is None:
            parsed_expected_safety_blocks = None
        else:
            parsed_expected_safety_blocks = max(0, int(expected_safety_blocks))
        return cls(
            name=name,
            user_input=str(user_input),
            category=str(payload.get("category") or ""),
            tags=_string_list(payload.get("tags", [])),
            purpose=str(payload.get("purpose") or ""),
            stability=str(payload.get("stability") or ""),
            session_key=str(payload.get("session_key") or payload.get("sessionKey") or f"eval:{name}"),
            channel=str(payload.get("channel") or "eval"),
            chat_id=(str(payload.get("chat_id") or payload.get("chatId")) if payload.get("chat_id") or payload.get("chatId") else None),
            metadata=dict(payload.get("metadata") or {}),
            expected_status=str(payload.get("expected_status") or payload.get("expectedStatus") or "completed"),
            require_final_answer=bool(payload.get("require_final_answer", payload.get("requireFinalAnswer", True))),
            expected_tools=_string_list(payload.get("expected_tools", payload.get("expectedTools", []))),
            forbidden_tools=_string_list(payload.get("forbidden_tools", payload.get("forbiddenTools", []))),
            allowed_extra_tools=_string_list(payload.get("allowed_extra_tools", payload.get("allowedExtraTools", []))),
            expected_command_substrings=_string_list(
                payload.get(
                    "expected_command_substrings",
                    payload.get("expectedCommandSubstrings", []),
                )
            ),
            expected_files_touched=_string_list(
                payload.get("expected_files_touched", payload.get("expectedFilesTouched", []))
            ),
            expected_files_written=_string_list(
                payload.get("expected_files_written", payload.get("expectedFilesWritten", []))
            ),
            expected_artifacts=_string_list(
                payload.get(
                    "expected_artifacts",
                    payload.get("expectedArtifacts", payload.get("expected_file_exists", [])),
                )
            ),
            expected_answer_contains=_string_list(
                payload.get("expected_answer_contains", payload.get("expectedAnswerContains", []))
            ),
            expected_safety_blocks=parsed_expected_safety_blocks,
            allow_tool_errors=bool(payload.get("allow_tool_errors", payload.get("allowToolErrors", True))),
            allow_safety_blocks=bool(payload.get("allow_safety_blocks", payload.get("allowSafetyBlocks", False))),
            disallow_unexpected_dangerous_tools=bool(
                payload.get(
                    "disallow_unexpected_dangerous_tools",
                    payload.get("disallowUnexpectedDangerousTools", True),
                )
            ),
        )

    @classmethod
    def from_file(cls, path: Path) -> "ReplayCase":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "input": self.user_input,
            "category": self.category,
            "tags": list(self.tags),
            "purpose": self.purpose,
            "stability": self.stability,
            "session_key": self.session_key,
            "channel": self.channel,
            "chat_id": self.chat_id or self.session_key,
            "metadata": dict(self.metadata),
            "expected_status": self.expected_status,
            "require_final_answer": self.require_final_answer,
            "expected_tools": list(self.expected_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "allowed_extra_tools": list(self.allowed_extra_tools),
            "expected_command_substrings": list(self.expected_command_substrings),
            "expected_files_touched": list(self.expected_files_touched),
            "expected_files_written": list(self.expected_files_written),
            "expected_artifacts": list(self.expected_artifacts),
            "expected_answer_contains": list(self.expected_answer_contains),
            "expected_safety_blocks": self.expected_safety_blocks,
            "allow_tool_errors": self.allow_tool_errors,
            "allow_safety_blocks": self.allow_safety_blocks,
            "disallow_unexpected_dangerous_tools": self.disallow_unexpected_dangerous_tools,
        }


@dataclass(slots=True)
class ReplayRunResult:
    """Result of one replay run."""

    case: ReplayCase
    trace_id: str
    trace_dir: Path
    scorecard: Scorecard


def _event_matches_case(event: Any, case: ReplayCase) -> bool:
    context = getattr(event, "context", None)
    if context is None:
        return False
    return (
        str(getattr(context, "session_key", "")) == case.session_key
        and str(getattr(context, "channel", "")) == case.channel
    )


async def run_replay_case(
    loop: Any,
    case: ReplayCase,
    store: TraceStore,
    *,
    trace_id: str | None = None,
) -> ReplayRunResult:
    """Run a replay case through the current runtime and score the fresh trace."""
    recorder = store.start_trace(
        trace_id=trace_id,
        metadata={
            "kind": "replay",
            "case": case.to_dict(),
        },
    )
    chat_id = case.chat_id or case.session_key
    recorder.record_input(
        user_input=case.user_input,
        session_key=case.session_key,
        channel=case.channel,
        chat_id=chat_id,
        metadata=case.metadata,
    )

    runtime_events = getattr(loop, "runtime_events", None)
    unsubscribe = None
    if runtime_events is not None and hasattr(runtime_events, "subscribe"):
        unsubscribe = runtime_events.subscribe(
            lambda event: recorder.record_runtime_event(event) if _event_matches_case(event, case) else None
        )

    try:
        result = await loop.process_message(
            case.user_input,
            session_key=case.session_key,
            channel=case.channel,
            chat_id=chat_id,
            metadata=case.metadata,
        )
        recorder.record_result(result)
        recorder.finalize(status="completed")
    except Exception as exc:
        recorder.append("runtime_exception", {"error": f"{type(exc).__name__}: {exc}"})
        recorder.finalize(status="error", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if callable(unsubscribe):
            unsubscribe()

    settings = getattr(loop, "settings", None)
    artifact_root = getattr(settings, "workspace_dir", None)
    scorecard = evaluate_trace(
        case,
        recorder.read_events(),
        trace_id=recorder.trace_id,
        artifact_root=artifact_root,
    )
    recorder.write_scorecard(scorecard)
    return ReplayRunResult(
        case=case,
        trace_id=recorder.trace_id,
        trace_dir=recorder.trace_dir,
        scorecard=scorecard,
    )
