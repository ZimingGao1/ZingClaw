from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from zingclaw.bus import (
    OutboundMessage,
    RuntimeEventBus,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    build_runtime_event_context,
)
from zingclaw.config import Settings
from zingclaw.evaluation import run_task_suite


@dataclass
class FakeLoopResult:
    final_content: str = "created notes/out.txt with hello task"
    stop_reason: str = "completed"
    error: str | None = None
    tools_used: list[str] = field(default_factory=lambda: ["write_file"])
    usage: dict[str, int] = field(default_factory=dict)


class FakeTaskLoop:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runtime_events = RuntimeEventBus()
        self.bus = None
        self.running = False
        self.closed = False

    def bind_bus(self, bus: Any) -> None:
        self.bus = bus

    def stop(self) -> None:
        self.running = False

    async def run(self) -> None:
        if self.bus is None:
            raise RuntimeError("missing bus")
        self.running = True
        while self.running:
            inbound = await self.bus.consume_inbound()
            result = await self.process_message(
                inbound.text,
                session_key=inbound.session_key,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                metadata=inbound.metadata,
            )
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    session_key=inbound.session_key,
                    text=result.final_content or "",
                    final=True,
                    metadata={
                        "stop_reason": result.stop_reason,
                        "error": result.error,
                        "tools_used": list(result.tools_used),
                        "usage": dict(result.usage),
                    },
                )
            )

    async def process_message(
        self,
        message: str,
        *,
        session_key: str,
        channel: str,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> FakeLoopResult:
        context = build_runtime_event_context(
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            metadata=metadata or {},
        )
        output_path = self.settings.workspace_dir / "notes" / "out.txt"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(f"hello task from: {message}\n", encoding="utf-8")
        await self.runtime_events.publish(
            ToolCallStarted(
                context=context,
                iteration=1,
                tool_name="write_file",
                tool_call_id="call_write",
                arguments={"path": "notes/out.txt"},
            )
        )
        await self.runtime_events.publish(
            ToolCallCompleted(
                context=context,
                iteration=1,
                tool_name="write_file",
                tool_call_id="call_write",
                result_preview="wrote notes/out.txt",
                is_error=False,
            )
        )
        await self.runtime_events.publish(
            TurnCompleted(
                context=context,
                stop_reason="completed",
                final_content="created notes/out.txt with hello task",
                tools_used=["write_file"],
            )
        )
        return FakeLoopResult()

    async def aclose(self) -> None:
        self.closed = True


def _make_fixture(fixtures: Path) -> None:
    workspace = fixtures / "basic_workspace" / "workspace"
    (workspace / "notes").mkdir(parents=True)
    (workspace / "test").mkdir()
    (workspace / "test" / "hello.py").write_text('print("hello")\n', encoding="utf-8")


def _write_task(path: Path, *, task_id: str, verifier_tool: str = "write_file") -> None:
    path.write_text(
        json.dumps(
            {
                "id": task_id,
                "category": "filesystem",
                "fixture": "basic_workspace",
                "input": "create an output note",
                "allowed_tools": ["write_file"],
                "expected_artifacts": [
                    {
                        "path": "notes/out.txt",
                        "exists": True,
                        "contains": ["hello task"],
                    }
                ],
                "verifiers": [
                    {"type": "status_completed"},
                    {"type": "tool_used", "name": verifier_tool},
                    {"type": "file_contains", "path": "notes/out.txt", "contains": ["hello task"]},
                    {"type": "workspace_diff_contains", "path": "notes/out.txt", "change": "created"},
                    {"type": "final_answer_contains", "contains": ["hello task"]},
                ],
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_run_task_suite_drives_fixture_trace_verifier_and_reports(tmp_path: Path) -> None:
    fixtures = tmp_path / "eval_fixtures"
    tasks = tmp_path / "eval_tasks" / "stable"
    tasks.mkdir(parents=True)
    _make_fixture(fixtures)
    _write_task(tasks / "write_note.json", task_id="write_note")

    result = await run_task_suite(
        Settings(_env_file=None, home_dir=tmp_path / "home"),
        tasks,
        fixtures_root=fixtures,
        runs_root=tmp_path / "eval_runs",
        run_id="run-1",
        loop_factory=FakeTaskLoop,
    )

    task_dir = tmp_path / "eval_runs" / "run-1" / "tasks" / "write_note"
    assert result.total == 1
    assert result.passed == 1
    assert result.failed == 0
    assert result.pass_rate == 100.0
    assert result.report_path == tmp_path / "eval_runs" / "run-1" / "summary.json"
    assert result.readable_report_path.exists()
    assert (task_dir / "workspace" / "notes" / "out.txt").read_text(encoding="utf-8").startswith("hello task")
    assert (task_dir / "traces" / result.cases[0].trace_id / "trace.jsonl").exists()
    assert (task_dir / "workspace_diff.json").exists()
    assert (task_dir / "verification.json").exists()
    assert (task_dir / "result.json").exists()
    assert result.cases[0].tools == ["write_file"]

    diff = json.loads((task_dir / "workspace_diff.json").read_text(encoding="utf-8"))
    assert "notes/out.txt" in diff["created"]
    summary = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert summary["passed"] == 1
    assert summary["cases"][0]["task_id"] == "write_note"


@pytest.mark.asyncio
async def test_run_task_suite_keeps_failed_task_report(tmp_path: Path) -> None:
    fixtures = tmp_path / "eval_fixtures"
    tasks = tmp_path / "eval_tasks" / "stable"
    tasks.mkdir(parents=True)
    _make_fixture(fixtures)
    _write_task(tasks / "strict_note.json", task_id="strict_note", verifier_tool="read_file")

    result = await run_task_suite(
        Settings(_env_file=None, home_dir=tmp_path / "home"),
        tasks,
        fixtures_root=fixtures,
        runs_root=tmp_path / "eval_runs",
        run_id="run-2",
        loop_factory=FakeTaskLoop,
    )

    assert result.total == 1
    assert result.passed == 0
    assert result.failed == 1
    assert result.cases[0].passed is False
    assert any("tool_used:read_file" in item for item in result.cases[0].failed_checks)
    readable = result.readable_report_path.read_text(encoding="utf-8")
    assert "# ZingClaw Task Eval Report" in readable
    assert "strict_note" in readable
