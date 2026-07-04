"""Local trace storage for replayable agent evidence."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zingclaw.bus import (
    RuntimeEventBus,
    RuntimeEventContext,
    SessionTurnStarted,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    TurnOutputDelta,
    TurnRunStatusChanged,
)

_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)
TRACE_DISABLED_METADATA_KEY = "_trace_disabled"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _is_secret_key(key: Any) -> bool:
    lowered = str(key).casefold()
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _json_safe(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_secret_key(key):
        return "[REDACTED]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(item_key): _json_safe(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return repr(value)


def runtime_event_type(event: Any) -> str:
    if isinstance(event, SessionTurnStarted):
        return "turn_started"
    if isinstance(event, TurnRunStatusChanged):
        return "status_changed"
    if isinstance(event, TurnOutputDelta):
        return "text_delta"
    if isinstance(event, ToolCallStarted):
        return "tool_started"
    if isinstance(event, ToolCallCompleted):
        return "tool_completed"
    if isinstance(event, TurnCompleted):
        return "turn_completed"
    return type(event).__name__


def _runtime_context_payload(context: RuntimeEventContext) -> dict[str, Any]:
    return {
        "channel": context.channel,
        "chat_id": context.chat_id,
        "session_key": context.session_key,
        "metadata": context.metadata,
    }


def _compact_runtime_event(event: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(event, SessionTurnStarted):
        return "turn_started", {}
    if isinstance(event, TurnRunStatusChanged):
        payload: dict[str, Any] = {"status": event.status}
        if event.iteration is not None:
            payload["iteration"] = event.iteration
        if event.detail is not None:
            payload["detail"] = event.detail
        return "status", payload
    if isinstance(event, TurnOutputDelta):
        payload = {"delta": event.delta}
        if event.iteration is not None:
            payload["iteration"] = event.iteration
        return "text_delta", payload
    if isinstance(event, ToolCallStarted):
        payload = {
            "iteration": event.iteration,
            "tool_name": event.tool_name,
            "tool_call_id": event.tool_call_id,
            "arguments": event.arguments,
        }
        return "tool_started", payload
    if isinstance(event, ToolCallCompleted):
        payload = {
            "iteration": event.iteration,
            "tool_name": event.tool_name,
            "tool_call_id": event.tool_call_id,
            "result_preview": event.result_preview,
            "is_error": event.is_error,
        }
        return "tool_completed", payload
    if isinstance(event, TurnCompleted):
        payload = {
            "stop_reason": event.stop_reason,
            "final_content": event.final_content,
            "tools_used": list(event.tools_used),
            "usage": dict(event.usage),
            "error": event.error,
            "latency_ms": event.latency_ms,
        }
        return "turn_completed", payload
    return type(event).__name__, {"value": repr(event)}


def _context_matches(event: Any, context: RuntimeEventContext) -> bool:
    event_context = getattr(event, "context", None)
    if event_context is None:
        return False
    return (
        str(getattr(event_context, "channel", "")) == context.channel
        and str(getattr(event_context, "chat_id", "")) == context.chat_id
        and str(getattr(event_context, "session_key", "")) == context.session_key
    )


class TraceRecorder:
    """Append-only JSONL recorder for one agent turn or replay run."""

    def __init__(self, trace_dir: Path, *, trace_id: str, metadata: dict[str, Any] | None = None) -> None:
        self.trace_id = trace_id
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.trace_dir / "trace.jsonl"
        self.metadata_path = self.trace_dir / "metadata.json"
        self.scorecard_path = self.trace_dir / "scorecard.json"
        self._seq = 0
        self._started_at = time.perf_counter()
        self._metadata = {
            "trace_id": trace_id,
            "created_at": _utcnow(),
            **_json_safe(metadata or {}),
        }
        self._write_metadata()

    def _write_metadata(self) -> None:
        self.metadata_path.write_text(
            json.dumps(self._metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def append(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._seq += 1
        row = {
            "seq": self._seq,
            "timestamp": _utcnow(),
            "elapsed_ms": max(0, int((time.perf_counter() - self._started_at) * 1000)),
            "type": event_type,
            **_json_safe(payload or {}),
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def record_input(
        self,
        *,
        user_input: str,
        session_key: str,
        channel: str,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.append(
            "user_input",
            {
                "input": user_input,
                "metadata": metadata or {},
            },
        )
        self._metadata.update(
            _json_safe(
                {
                    "session_key": session_key,
                    "channel": channel,
                    "chat_id": chat_id,
                }
            )
        )
        self._write_metadata()

    def record_runtime_event(self, event: Any) -> None:
        event_type, payload = _compact_runtime_event(event)
        self.append(event_type, payload)

    def record_result(self, result: Any) -> None:
        self.append(
            "turn_result",
            {
                "stop_reason": getattr(result, "stop_reason", None),
                "error": getattr(result, "error", None),
                "tools_used": list(getattr(result, "tools_used", []) or []),
                "usage": dict(getattr(result, "usage", {}) or {}),
            },
        )

    def finalize(self, **updates: Any) -> None:
        self._metadata.update(_json_safe(updates))
        self._metadata["updated_at"] = _utcnow()
        self._write_metadata()

    def read_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events

    def write_scorecard(self, scorecard: Any) -> None:
        to_dict = getattr(scorecard, "to_dict", None)
        payload = to_dict() if callable(to_dict) else _json_safe(scorecard)
        self.scorecard_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class RuntimeTraceCapture:
    """Subscribe to runtime events for one turn and write a compact local trace."""

    def __init__(
        self,
        recorder: TraceRecorder,
        runtime_events: RuntimeEventBus,
        context: RuntimeEventContext,
        *,
        include_text_deltas: bool = False,
    ) -> None:
        self.recorder = recorder
        self.trace_id = recorder.trace_id
        self._runtime_events = runtime_events
        self._context = context
        self._include_text_deltas = include_text_deltas
        self._unsubscribe = runtime_events.subscribe(self._record_if_current_turn)

    def _record_if_current_turn(self, event: Any) -> None:
        if not _context_matches(event, self._context):
            return
        if isinstance(event, TurnOutputDelta) and not self._include_text_deltas:
            return
        self.recorder.record_runtime_event(event)

    def record_result(self, result: Any) -> None:
        self.recorder.record_result(result)

    def finalize(self, **updates: Any) -> None:
        if callable(self._unsubscribe):
            self._unsubscribe()
            self._unsubscribe = None
        self.recorder.finalize(**updates)


class TraceStore:
    """Filesystem-backed trace directory manager."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)

    def start_trace(
        self,
        *,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceRecorder:
        resolved_id = trace_id or _trace_id()
        return TraceRecorder(self.root / resolved_id, trace_id=resolved_id, metadata=metadata)

    def start_runtime_capture(
        self,
        *,
        runtime_events: RuntimeEventBus,
        context: RuntimeEventContext,
        user_input: str,
        workflow_name: str = "agent_turn",
        metadata: dict[str, Any] | None = None,
        include_text_deltas: bool = False,
    ) -> RuntimeTraceCapture:
        recorder = self.start_trace(
            metadata={
                "workflow_name": workflow_name,
                "context": _runtime_context_payload(context),
                **(metadata or {}),
            }
        )
        recorder.record_input(
            user_input=user_input,
            session_key=context.session_key,
            channel=context.channel,
            chat_id=context.chat_id,
            metadata=context.metadata,
        )
        return RuntimeTraceCapture(
            recorder,
            runtime_events,
            context,
            include_text_deltas=include_text_deltas,
        )

    def trace_dir(self, trace_id: str) -> Path:
        return self.root / trace_id

    def resolve_trace_path(self, trace_id_or_dir: str | Path) -> Path:
        path = Path(trace_id_or_dir)
        if path.exists():
            return path.expanduser().resolve()
        return self.trace_dir(str(trace_id_or_dir))

    def read_events(self, trace_id_or_dir: str | Path) -> list[dict[str, Any]]:
        path = self.resolve_trace_path(trace_id_or_dir)
        events_path = path if path.is_file() else path / "trace.jsonl"
        if not events_path.exists():
            return []
        return [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def read_metadata(self, trace_id_or_dir: str | Path) -> dict[str, Any]:
        path = self.resolve_trace_path(trace_id_or_dir)
        trace_dir = path.parent if path.is_file() else path
        metadata_path = trace_dir / "metadata.json"
        if not metadata_path.exists():
            return {}
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
