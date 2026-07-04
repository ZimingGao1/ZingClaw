from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from zingclaw.bus import (
    RuntimeEventBus,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    build_runtime_event_context,
)
from zingclaw.evaluation import TraceStore, discover_replay_cases, run_replay_suite


@dataclass
class FakeLoopResult:
    final_content: str = "done"
    stop_reason: str = "completed"
    error: str | None = None
    tools_used: list[str] = field(default_factory=lambda: ["echo_tool"])
    usage: dict[str, int] = field(default_factory=dict)


class FakeReplayLoop:
    def __init__(self) -> None:
        self.runtime_events = RuntimeEventBus()
        self.calls: list[str] = []

    async def process_message(
        self,
        message: str,
        *,
        session_key: str,
        channel: str,
        chat_id: str,
        metadata=None,
    ) -> FakeLoopResult:
        self.calls.append(message)
        context = build_runtime_event_context(
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            metadata=metadata or {},
        )
        await self.runtime_events.publish(
            ToolCallStarted(
                context=context,
                iteration=1,
                tool_name="echo_tool",
                tool_call_id="call_1",
                arguments={"value": message},
            )
        )
        await self.runtime_events.publish(
            ToolCallCompleted(
                context=context,
                iteration=1,
                tool_name="echo_tool",
                tool_call_id="call_1",
                result_preview="ok",
                is_error=False,
            )
        )
        await self.runtime_events.publish(
            TurnCompleted(
                context=context,
                stop_reason="completed",
                final_content="done",
                tools_used=["echo_tool"],
            )
        )
        return FakeLoopResult()


def _write_case(path: Path, *, name: str, expected_tools: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "name": name,
                "input": f"run {name}",
                "category": "tooling",
                "tags": ["regression", "tooling"],
                "purpose": f"Protect {name}",
                "session_key": f"eval:{name}",
                "expected_tools": expected_tools,
                "allow_tool_errors": False,
            }
        ),
        encoding="utf-8",
    )


def test_discover_replay_cases_supports_file_and_directory(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    first = suite / "a.json"
    second = suite / "nested" / "b.json"
    second.parent.mkdir()
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    (suite / "suite_report.json").write_text("{}", encoding="utf-8")
    (suite / "manifest.json").write_text("{}", encoding="utf-8")

    assert discover_replay_cases(first) == [first.resolve()]
    assert discover_replay_cases(suite) == [first.resolve(), second.resolve()]


@pytest.mark.asyncio
async def test_run_replay_suite_writes_report_and_keeps_failed_cases(tmp_path: Path) -> None:
    suite = tmp_path / "replay_cases" / "regression"
    suite.mkdir(parents=True)
    _write_case(suite / "pass.json", name="pass_case", expected_tools=["echo_tool"])
    _write_case(suite / "fail.json", name="fail_case", expected_tools=["missing_tool"])
    trace_root = tmp_path / "traces"

    result = await run_replay_suite(FakeReplayLoop(), suite, TraceStore(trace_root))

    assert result.total == 2
    assert result.passed == 1
    assert result.failed == 1
    assert result.pass_rate == 50.0
    assert result.report_path == suite / "suite_report.json"
    assert result.readable_report_path == suite / "suite_report.md"
    assert result.report_path.exists()
    assert result.readable_report_path.exists()
    assert len([path for path in trace_root.iterdir() if path.is_dir()]) == 2

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["total"] == 2
    assert report["passed"] == 1
    assert report["readable_report_path"] == str(result.readable_report_path)
    failed = next(item for item in report["cases"] if item["case_name"] == "fail_case")
    assert failed["passed"] is False
    assert failed["category"] == "tooling"
    assert failed["trace_dir"]
    assert any("expected_tool:missing_tool" in item for item in failed["failed_checks"])

    readable = result.readable_report_path.read_text(encoding="utf-8")
    assert "# ZingClaw Eval Suite Report" in readable
    assert "## Summary" in readable
    assert "## Failed Cases" in readable
    assert "### fail_case" in readable
    assert "fail_case" in readable
    assert "expected_tool:missing_tool" in readable
    assert "## Case Details" in readable
    assert "## Case Metadata" in readable
