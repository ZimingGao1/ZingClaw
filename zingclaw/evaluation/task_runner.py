"""Run structured task contracts through the full local Agent runtime."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zingclaw.bus import InboundMessage, MessageBus, OutboundMessage
from zingclaw.config import Settings, ensure_runtime_dirs
from zingclaw.evaluation.fixtures import TaskWorkspace, prepare_task_workspace
from zingclaw.evaluation.task_contract import TaskContract, load_task_contract
from zingclaw.evaluation.trace import TRACE_DISABLED_METADATA_KEY, TraceStore
from zingclaw.evaluation.verifiers import VerificationReport, evaluate_task_contract


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _json_safe(to_dict())
    return repr(value)


def _discover_task_contract_paths(path: Path, *, pattern: str = "*.json") -> list[Path]:
    target = Path(path).expanduser().resolve(strict=False)
    if target.is_file():
        return [target]
    if not target.exists():
        raise FileNotFoundError(f"task path does not exist: {target}")
    return sorted(item for item in target.rglob(pattern) if item.is_file())


def _event_matches_task(event: Any, *, session_key: str, channel: str) -> bool:
    context = getattr(event, "context", None)
    if context is None:
        return False
    return (
        str(getattr(context, "session_key", "")) == session_key
        and str(getattr(context, "channel", "")) == channel
    )


def _failed_check_names(report: VerificationReport, *, error: str | None = None) -> list[str]:
    failed = [
        f"{check.name}: {check.detail}" if check.detail else check.name
        for check in report.checks
        if not check.passed
    ]
    if error:
        failed.insert(0, error)
    return failed


def _task_settings(base: Settings, workspace: TaskWorkspace) -> Settings:
    settings = base.model_copy(deep=True)
    run_dir = workspace.run_dir
    settings.home_dir = run_dir
    settings.paths.workspace_dir = workspace.workspace_dir
    settings.paths.sessions_dir = run_dir / "sessions"
    settings.paths.memory_dir = run_dir / "memory"
    settings.paths.logs_dir = run_dir / "logs"
    settings.paths.skills_dir = base.skills_dir
    settings.tracing.enabled = True
    settings.tracing.traces_dir = run_dir / "traces"
    settings.workspace_dir = settings.paths.workspace_dir
    settings.sessions_dir = settings.paths.sessions_dir
    settings.memory_dir = settings.paths.memory_dir
    settings.logs_dir = settings.paths.logs_dir
    settings.skills_dir = settings.paths.skills_dir
    return settings


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _close_loop_sync(loop: Any) -> None:
    close = getattr(loop, "close", None)
    if callable(close):
        close()


async def _close_loop_async(loop: Any) -> None:
    aclose = getattr(loop, "aclose", None)
    if callable(aclose):
        await aclose()
        return
    await asyncio.to_thread(_close_loop_sync, loop)


@dataclass(slots=True)
class _TaskTurnResult:
    final_content: str | None = None
    stop_reason: str = "completed"
    error: str | None = None
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


def _turn_result_from_outbound(outbound: OutboundMessage) -> _TaskTurnResult:
    metadata = dict(outbound.metadata or {})
    error = metadata.get("error")
    return _TaskTurnResult(
        final_content=outbound.text or None,
        stop_reason=str(metadata.get("stop_reason") or "completed"),
        error=error if isinstance(error, str) else None,
        tools_used=[str(tool) for tool in metadata.get("tools_used", []) or []],
        usage=dict(metadata.get("usage", {}) or {}),
    )


async def _run_task_turn(
    loop: Any,
    *,
    user_input: str,
    session_key: str,
    channel: str,
    chat_id: str,
    metadata: dict[str, Any],
) -> Any:
    """Drive the runtime through MessageBus when the loop supports it."""
    if not hasattr(loop, "bind_bus") or not hasattr(loop, "run"):
        return await loop.process_message(
            user_input,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            metadata=metadata,
        )

    bus = MessageBus()
    loop.bind_bus(bus)
    loop_task = asyncio.create_task(loop.run())
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel=channel,
                chat_id=chat_id,
                session_key=session_key,
                sender_id="eval",
                text=user_input,
                metadata=metadata,
            )
        )
        while True:
            outbound = await bus.consume_outbound()
            if outbound.channel == channel and outbound.session_key == session_key and outbound.final:
                return _turn_result_from_outbound(outbound)
    finally:
        stop = getattr(loop, "stop", None)
        if callable(stop):
            stop()
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)


@dataclass(slots=True)
class TaskRunCaseResult:
    """Result for one structured task contract run."""

    task_id: str
    contract_path: str
    passed: bool
    score: int
    category: str = ""
    tags: list[str] = field(default_factory=list)
    purpose: str = ""
    run_dir: str = ""
    workspace_dir: str = ""
    trace_id: str = ""
    trace_dir: str = ""
    stop_reason: str = ""
    tools: list[str] = field(default_factory=list)
    tool_error_count: int = 0
    failed_checks: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "contract_path": self.contract_path,
            "passed": self.passed,
            "score": self.score,
            "category": self.category,
            "tags": list(self.tags),
            "purpose": self.purpose,
            "run_dir": self.run_dir,
            "workspace_dir": self.workspace_dir,
            "trace_id": self.trace_id,
            "trace_dir": self.trace_dir,
            "stop_reason": self.stop_reason,
            "tools": list(self.tools),
            "tool_error_count": self.tool_error_count,
            "failed_checks": list(self.failed_checks),
            "error": self.error,
        }


@dataclass(slots=True)
class TaskRunSuiteResult:
    """Aggregate result for one task-contract suite run."""

    task_path: Path
    run_id: str
    run_dir: Path
    report_path: Path
    readable_report_path: Path
    total: int
    passed: int
    failed: int
    pass_rate: float
    cases: list[TaskRunCaseResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": _utcnow(),
            "task_path": str(self.task_path),
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "report_path": str(self.report_path),
            "readable_report_path": str(self.readable_report_path),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.pass_rate,
            "cases": [item.to_dict() for item in self.cases],
        }


async def run_task_contract(
    contract: TaskContract,
    *,
    contract_path: Path,
    settings: Settings,
    fixtures_root: Path,
    runs_root: Path,
    run_id: str,
    loop_factory: Callable[[Settings], Any],
) -> TaskRunCaseResult:
    """Run one task contract in an isolated fixture workspace."""
    workspace = prepare_task_workspace(
        contract,
        fixtures_root=fixtures_root,
        runs_root=runs_root,
        run_id=run_id,
    )
    task_settings = _task_settings(settings, workspace)
    ensure_runtime_dirs(task_settings)
    _write_json(workspace.run_dir / "task_contract.json", contract.to_dict())
    _write_json(workspace.run_dir / "workspace_before.json", workspace.before.to_dict())

    trace_store = TraceStore(workspace.run_dir / "traces")
    session_key = f"evaltask:{contract.id}"
    channel = "eval_task"
    chat_id = session_key
    public_metadata = {
        "eval_task_id": contract.id,
        "eval_task_category": contract.category,
        "eval_task_tags": list(contract.tags),
    }
    public_metadata.update(contract.metadata)
    recorder = trace_store.start_trace(
        metadata={
            "kind": "task_contract",
            "task": contract.to_dict(),
            "contract_path": str(contract_path),
            "workspace_dir": str(workspace.workspace_dir),
        }
    )
    recorder.record_input(
        user_input=contract.input,
        session_key=session_key,
        channel=channel,
        chat_id=chat_id,
        metadata=public_metadata,
    )

    loop = loop_factory(task_settings)
    runtime_events = getattr(loop, "runtime_events", None)
    unsubscribe = None
    if runtime_events is not None and hasattr(runtime_events, "subscribe"):
        unsubscribe = runtime_events.subscribe(
            lambda event: recorder.record_runtime_event(event)
            if _event_matches_task(event, session_key=session_key, channel=channel)
            else None
        )

    error: str | None = None
    result: Any = None
    try:
        result = await asyncio.wait_for(
            _run_task_turn(
                loop,
                user_input=contract.input,
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
                metadata={**public_metadata, TRACE_DISABLED_METADATA_KEY: True},
            ),
            timeout=contract.timeout_seconds,
        )
        recorder.record_result(result)
        recorder.finalize(status=getattr(result, "stop_reason", "completed"), error=getattr(result, "error", None))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        recorder.append("runtime_exception", {"error": error})
        recorder.finalize(status="error", error=error)
    finally:
        if callable(unsubscribe):
            unsubscribe()
        await _close_loop_async(loop)

    diff = workspace.diff()
    events = recorder.read_events()
    verification = evaluate_task_contract(
        contract,
        events,
        workspace_dir=workspace.workspace_dir,
        workspace_diff=diff,
    )
    if error:
        verification.passed = False
    recorder.write_scorecard(verification)
    _write_json(workspace.run_dir / "workspace_diff.json", diff.to_dict())
    _write_json(workspace.run_dir / "verification.json", verification.to_dict())

    metrics = dict(verification.metrics)
    case = TaskRunCaseResult(
        task_id=contract.id,
        contract_path=str(contract_path),
        passed=verification.passed,
        score=verification.score,
        category=contract.category,
        tags=list(contract.tags),
        purpose=contract.purpose,
        run_dir=str(workspace.run_dir),
        workspace_dir=str(workspace.workspace_dir),
        trace_id=recorder.trace_id,
        trace_dir=str(recorder.trace_dir),
        stop_reason=str(metrics.get("stop_reason") or getattr(result, "stop_reason", "") or ("error" if error else "")),
        tools=[str(tool) for tool in metrics.get("tools", [])],
        tool_error_count=int(metrics.get("tool_error_count") or 0),
        failed_checks=_failed_check_names(verification, error=error),
        error=error,
    )
    _write_json(workspace.run_dir / "result.json", case.to_dict())
    return case


def _status_label(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _escape_table(value: Any) -> str:
    text = str(value or "").replace("\r\n", " ").replace("\n", " ").strip()
    return text.replace("|", "\\|")


def _short_path(value: str | Path) -> str:
    path = Path(value)
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _write_readable_report(result: TaskRunSuiteResult, *, generated_at: str) -> None:
    failed_cases = [item for item in result.cases if not item.passed]
    lines = [
        "# ZingClaw Task Eval Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Run id: `{result.run_id}`",
        f"- Tasks: `{result.task_path}`",
        f"- Run dir: `{result.run_dir}`",
        f"- Total: `{result.total}`",
        f"- Passed: `{result.passed}`",
        f"- Failed: `{result.failed}`",
        f"- Pass rate: `{result.pass_rate:.2f}%`",
        "",
    ]
    if failed_cases:
        lines.extend(["## Failed Tasks", ""])
        for item in failed_cases:
            lines.extend(
                [
                    f"### {item.task_id}",
                    "",
                    f"- Score: `{item.score}`",
                    f"- Category: `{item.category or '-'}`",
                    f"- Trace: `{_short_path(item.trace_dir)}`",
                    "- Failed checks:",
                ]
            )
            for failure in item.failed_checks or ["unknown failure"]:
                lines.append(f"  - {failure}")
            lines.append("")
    else:
        lines.extend(["## Failed Tasks", "", "No failed tasks.", ""])

    lines.extend(
        [
            "## Task Details",
            "",
            "| Status | Task | Score | Category | Tools | Trace |",
            "| --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for item in result.cases:
        lines.append(
            "| "
            f"{_status_label(item.passed)} | "
            f"{_escape_table(item.task_id)} | "
            f"{item.score} | "
            f"{_escape_table(item.category or '-')} | "
            f"{_escape_table(', '.join(item.tools) or '-')} | "
            f"`{_escape_table(_short_path(item.trace_dir))}` |"
        )
    lines.append("")
    result.readable_report_path.write_text("\n".join(lines), encoding="utf-8")


async def run_task_suite(
    settings: Settings,
    task_path: Path,
    *,
    fixtures_root: Path = Path("eval_fixtures"),
    runs_root: Path = Path("eval_runs"),
    pattern: str = "*.json",
    run_id: str | None = None,
    loop_factory: Callable[[Settings], Any] | None = None,
) -> TaskRunSuiteResult:
    """Run every task contract under a file or directory and write aggregate reports."""
    from zingclaw.agent import AgentLoop

    suite = Path(task_path).expanduser().resolve(strict=False)
    actual_run_id = run_id or _default_run_id()
    root = Path(runs_root).expanduser().resolve(strict=False)
    run_dir = root / actual_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    factory = loop_factory or AgentLoop.from_settings
    contract_paths = _discover_task_contract_paths(suite, pattern=pattern)
    if not contract_paths:
        raise ValueError(f"no task contracts matched {pattern!r} under {suite}")

    cases: list[TaskRunCaseResult] = []
    for contract_path in contract_paths:
        try:
            contract = load_task_contract(contract_path)
            cases.append(
                await run_task_contract(
                    contract,
                    contract_path=contract_path,
                    settings=settings,
                    fixtures_root=fixtures_root,
                    runs_root=root,
                    run_id=actual_run_id,
                    loop_factory=factory,
                )
            )
        except Exception as exc:
            cases.append(
                TaskRunCaseResult(
                    task_id=contract_path.stem,
                    contract_path=str(contract_path),
                    passed=False,
                    score=0,
                    run_dir=str(run_dir / "tasks" / contract_path.stem),
                    error=f"{type(exc).__name__}: {exc}",
                    failed_checks=[f"{type(exc).__name__}: {exc}"],
                )
            )

    passed = sum(1 for item in cases if item.passed)
    total = len(cases)
    failed = total - passed
    pass_rate = round((passed / total) * 100, 2) if total else 0.0
    report_path = run_dir / "summary.json"
    readable_report_path = run_dir / "summary.md"
    generated_at = _utcnow()
    result = TaskRunSuiteResult(
        task_path=suite,
        run_id=actual_run_id,
        run_dir=run_dir,
        report_path=report_path,
        readable_report_path=readable_report_path,
        total=total,
        passed=passed,
        failed=failed,
        pass_rate=pass_rate,
        cases=cases,
    )
    payload = result.to_dict()
    payload["generated_at"] = generated_at
    _write_json(report_path, payload)
    _write_readable_report(result, generated_at=generated_at)
    return result
