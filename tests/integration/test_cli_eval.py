from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import zingclaw.cli.main as cli_main
from zingclaw.bus import (
    RuntimeEventBus,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    TurnRunStatusChanged,
    build_runtime_event_context,
)
from zingclaw.cli.main import app
from zingclaw.config.settings import reset_settings_cache
from zingclaw.evaluation import TraceStore
from zingclaw.memory import MemoryStore
from zingclaw.providers import LLMProvider, LLMResponse


@dataclass
class FakeLoopResult:
    final_content: str
    stop_reason: str = "completed"
    error: str | None = None
    tools_used: list[str] = field(default_factory=lambda: ["echo_tool"])
    usage: dict[str, int] = field(default_factory=dict)
    trace_id: str | None = "trace_fake"


class FakeEvalLoop:
    def __init__(self) -> None:
        self.runtime_events = RuntimeEventBus()
        self.closed = False
        self.calls: list[tuple[str, str, str]] = []

    async def process_message(self, message: str, *, session_key: str, channel: str, chat_id: str, metadata=None, **kwargs):
        del kwargs
        self.calls.append((session_key, channel, message))
        context = build_runtime_event_context(
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            metadata=metadata or {},
        )
        await self.runtime_events.publish(TurnRunStatusChanged(context=context, status="calling_model"))
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
        return FakeLoopResult(final_content="done")

    def close(self) -> None:
        self.closed = True


class FakeDreamProvider(LLMProvider):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_text_delta=None,
    ) -> LLMResponse:
        del tools, model, max_tokens, temperature, reasoning_effort, tool_choice, on_text_delta
        assert "CLI-visible tests" in messages[1]["content"]
        return LLMResponse(content="- User values CLI-visible tests", finish_reason="stop")

    def get_default_model(self) -> str:
        return "dream-model"


class FakeDreamLoop:
    def __init__(self, memory_dir: Path) -> None:
        self.memory = MemoryStore(memory_dir, max_history_entries=20)
        self.memory.append_history("user prefers CLI-visible tests", session_key="cli:demo")
        self.provider = FakeDreamProvider(api_key=None, api_base=None)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_eval_run_cli_writes_trace_and_scorecard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ZINGCLAW_HOME", str(tmp_path))
    reset_settings_cache()
    fake_loop = FakeEvalLoop()
    monkeypatch.setattr(cli_main.AgentLoop, "from_settings", classmethod(lambda cls, settings: fake_loop))
    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "cli_eval",
                "input": "hello",
                "session_key": "eval:cli",
                "expected_tools": ["echo_tool"],
            }
        ),
        encoding="utf-8",
    )
    trace_root = tmp_path / "traces"

    result = CliRunner().invoke(
        app,
        ["eval", "run", str(case_path), "--trace-root", str(trace_root)],
    )

    assert result.exit_code == 0
    assert "Replay cli_eval: score 100/100" in result.stdout
    trace_dirs = [path for path in trace_root.iterdir() if path.is_dir()]
    assert len(trace_dirs) == 1
    assert (trace_dirs[0] / "trace.jsonl").exists()
    assert (trace_dirs[0] / "scorecard.json").exists()
    assert fake_loop.closed is True

    reset_settings_cache()


def test_eval_run_suite_cli_writes_aggregate_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ZINGCLAW_HOME", str(tmp_path))
    reset_settings_cache()
    fake_loop = FakeEvalLoop()
    monkeypatch.setattr(cli_main.AgentLoop, "from_settings", classmethod(lambda cls, settings: fake_loop))
    suite = tmp_path / "replay_cases" / "regression"
    suite.mkdir(parents=True)
    (suite / "pass.json").write_text(
        json.dumps(
            {
                "name": "suite_pass",
                "input": "hello",
                "session_key": "eval:suite_pass",
                "expected_tools": ["echo_tool"],
            }
        ),
        encoding="utf-8",
    )
    (suite / "fail.json").write_text(
        json.dumps(
            {
                "name": "suite_fail",
                "input": "hello again",
                "session_key": "eval:suite_fail",
                "expected_tools": ["missing_tool"],
            }
        ),
        encoding="utf-8",
    )
    trace_root = tmp_path / "traces"

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "run-suite",
            str(suite),
            "--trace-root",
            str(trace_root),
            "--readable-report",
            str(suite / "readable.md"),
            "--no-fail-on-regression",
        ],
    )

    assert result.exit_code == 0
    assert "Eval suite: 1/2 passed" in result.stdout
    assert "readable report:" in result.stdout
    assert "suite_fail" in result.stdout
    report_path = suite / "suite_report.json"
    readable_path = suite / "readable.md"
    assert report_path.exists()
    assert readable_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["total"] == 2
    assert report["failed"] == 1
    assert report["readable_report_path"] == str(readable_path.resolve())
    assert "## Failed Cases" in readable_path.read_text(encoding="utf-8")
    assert len([path for path in trace_root.iterdir() if path.is_dir()]) == 2
    assert fake_loop.closed is True

    reset_settings_cache()


def test_memory_dream_cli_updates_memory_from_archived_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ZINGCLAW_HOME", str(tmp_path))
    reset_settings_cache()
    fake_loop = FakeDreamLoop(tmp_path / "memory")
    monkeypatch.setattr(cli_main.AgentLoop, "from_settings", classmethod(lambda cls, settings: fake_loop))

    result = CliRunner().invoke(app, ["memory", "dream", "--max-entries", "1"])

    assert result.exit_code == 0
    assert "Dream processed 1 archived entries" in result.stdout
    assert "updated MEMORY.md" in result.stdout
    assert "User values CLI-visible tests" in fake_loop.memory.read_memory()
    assert fake_loop.memory.get_last_dream_cursor() == 1
    assert fake_loop.closed is True

    reset_settings_cache()


def test_trace_seed_cli_writes_plain_question_dataset(tmp_path: Path) -> None:
    output = tmp_path / "starter.json"

    result = CliRunner().invoke(app, ["trace", "seed", "--output", str(output)])

    assert result.exit_code == 0
    assert "Wrote" in result.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    questions = payload["questions"]
    assert len(questions) >= 20
    assert {item["category"] for item in questions} >= {"chat", "filesystem", "shell", "safety"}
    assert "expected_tools" not in questions[0]


def test_trace_inspect_cli_prints_readable_timeline(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    recorder = store.start_trace(
        trace_id="trace_inspect_demo",
        metadata={
            "workflow_name": "agent_turn",
            "session_key": "traceq:demo",
            "model": "fake-model",
            "status": "completed",
        },
    )
    recorder.record_input(
        user_input="read a file",
        session_key="traceq:demo",
        channel="trace",
        chat_id="traceq:demo",
    )
    recorder.append("status", {"status": "calling_model", "iteration": 1})
    recorder.append(
        "tool_started",
        {"iteration": 1, "tool_name": "read_file", "tool_call_id": "call_1", "arguments": {"path": "demo.txt"}},
    )
    recorder.append(
        "tool_completed",
        {
            "iteration": 1,
            "tool_name": "read_file",
            "tool_call_id": "call_1",
            "result_preview": "ok",
            "is_error": False,
        },
    )
    recorder.append(
        "turn_completed",
        {
            "stop_reason": "completed",
            "final_content": "final visible answer",
            "tools_used": ["read_file"],
            "usage": {},
            "error": None,
            "latency_ms": 42,
        },
    )
    recorder.append("turn_result", {"stop_reason": "completed", "tools_used": ["read_file"], "usage": {}})
    recorder.finalize(status="completed")

    result = CliRunner().invoke(app, ["trace", "inspect", str(recorder.trace_dir)])

    assert result.exit_code == 0
    assert "Trace Timeline" in result.stdout
    assert "tool_started" in result.stdout
    assert "read_file start" in result.stdout
    assert "Final Answer" in result.stdout
    assert result.stdout.count("final visible answer") == 1


def test_eval_curate_cli_writes_replay_case_and_report(tmp_path: Path) -> None:
    trace_root = tmp_path / "traces"
    output_dir = tmp_path / "replay_cases"
    recorder = TraceStore(trace_root).start_trace(
        trace_id="curate_demo",
        metadata={"workflow_name": "agent_turn", "status": "completed"},
    )
    recorder.record_input(
        user_input="read demo.txt",
        session_key="traceq:curate_demo",
        channel="trace",
        chat_id="traceq:curate_demo",
        metadata={"trace_question_id": "curate_demo"},
    )
    recorder.append(
        "tool_started",
        {
            "iteration": 1,
            "tool_name": "read_file",
            "tool_call_id": "call_1",
            "arguments": {"path": "demo.txt"},
        },
    )
    recorder.append(
        "tool_completed",
        {
            "iteration": 1,
            "tool_name": "read_file",
            "tool_call_id": "call_1",
            "result_preview": "ok",
            "is_error": False,
        },
    )
    recorder.append(
        "turn_completed",
        {
            "stop_reason": "completed",
            "final_content": "done",
            "tools_used": ["read_file"],
            "usage": {},
        },
    )
    recorder.finalize(status="completed")

    result = CliRunner().invoke(app, ["eval", "curate", str(trace_root), "--output", str(output_dir)])

    assert result.exit_code == 0
    assert "1 regression" in result.stdout
    assert (output_dir / "regression" / "curate_demo.json").exists()
    assert (output_dir / "curation_report.json").exists()


def test_eval_organize_cli_writes_manifest_and_can_normalize(tmp_path: Path) -> None:
    root = tmp_path / "replay_cases"
    regression = root / "regression"
    regression.mkdir(parents=True)
    case_path = regression / "read_demo.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "read_demo",
                "input": "read demo.txt",
                "expected_tools": ["read_file"],
                "expected_files_touched": ["demo.txt"],
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["eval", "organize", str(root), "--normalize"])

    assert result.exit_code == 0
    assert "Replay dataset: 1 regression, 0 candidate, 0 issues" in result.stdout
    assert (root / "manifest.json").exists()
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    assert payload["category"] == "filesystem"
    assert "tool:read_file" in payload["tags"]


def test_trace_run_cli_runs_plain_questions_and_prints_trace_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ZINGCLAW_HOME", str(tmp_path))
    reset_settings_cache()
    fake_loop = FakeEvalLoop()
    monkeypatch.setattr(cli_main.AgentLoop, "from_settings", classmethod(lambda cls, settings: fake_loop))
    question_path = tmp_path / "questions.json"
    question_path.write_text(
        json.dumps(
            {
                "version": 1,
                "name": "tiny",
                "description": "tiny trace question set",
                "questions": [
                    {"id": "q1", "category": "chat", "input": "hello"},
                    {"id": "q2", "category": "shell", "input": "run python --version"},
                ],
            }
        ),
        encoding="utf-8",
    )
    trace_root = tmp_path / "traces"

    result = CliRunner().invoke(
        app,
        [
            "trace",
            "run",
            str(question_path),
            "--trace-root",
            str(trace_root),
            "--offset",
            "1",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "Trace Question Runs" in result.stdout
    assert "q1" not in result.stdout
    assert "q2" in result.stdout
    assert fake_loop.calls == [("traceq:q2", "trace", "run python --version")]
    assert fake_loop.closed is True

    reset_settings_cache()
