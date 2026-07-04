from __future__ import annotations

import json
from pathlib import Path

from zingclaw.evaluation import TraceStore, curate_traces


def _write_trace(
    root: Path,
    *,
    trace_id: str,
    user_input: str,
    tool_name: str | None = None,
    tool_args: dict | None = None,
    final_content: str = "done",
) -> None:
    recorder = TraceStore(root).start_trace(
        trace_id=trace_id,
        metadata={"workflow_name": "agent_turn", "status": "completed"},
    )
    recorder.record_input(
        user_input=user_input,
        session_key=f"traceq:{trace_id}",
        channel="trace",
        chat_id=f"traceq:{trace_id}",
        metadata={"trace_question_id": trace_id},
    )
    if tool_name is not None:
        recorder.append(
            "tool_started",
            {
                "iteration": 1,
                "tool_name": tool_name,
                "tool_call_id": "call_1",
                "arguments": dict(tool_args or {}),
            },
        )
        recorder.append(
            "tool_completed",
            {
                "iteration": 1,
                "tool_name": tool_name,
                "tool_call_id": "call_1",
                "result_preview": "ok",
                "is_error": False,
            },
        )
    recorder.append(
        "turn_completed",
        {
            "stop_reason": "completed",
            "final_content": final_content,
            "tools_used": [tool_name] if tool_name else [],
            "usage": {},
            "error": None,
            "latency_ms": 10,
        },
    )
    recorder.append(
        "turn_result",
        {"stop_reason": "completed", "tools_used": [tool_name] if tool_name else [], "usage": {}},
    )
    recorder.finalize(status="completed")


def test_curate_traces_writes_regression_candidates_and_report(tmp_path: Path) -> None:
    trace_root = tmp_path / "traces"
    output_dir = tmp_path / "replay_cases"
    _write_trace(
        trace_root,
        trace_id="read_hello_py",
        user_input="read test/hello.py",
        tool_name="read_file",
        tool_args={"path": "test/hello.py"},
    )
    _write_trace(
        trace_root,
        trace_id="web_weather",
        user_input="search weather",
        tool_name="web_search",
        tool_args={"query": "weather"},
    )
    _write_trace(trace_root, trace_id="chat_only", user_input="hello", tool_name=None)

    result = curate_traces(trace_root, output_dir=output_dir)

    assert result.counts == {"regression": 1, "candidate": 1, "discard": 1}
    regression_case = output_dir / "regression" / "read_hello_py.json"
    candidate_case = output_dir / "candidates" / "web_weather.json"
    assert regression_case.exists()
    assert candidate_case.exists()
    assert result.report_path.exists()

    payload = json.loads(regression_case.read_text(encoding="utf-8"))
    assert payload["name"] == "read_hello_py"
    assert payload["input"] == "read test/hello.py"
    assert payload["expected_tools"] == ["read_file"]
    assert payload["expected_files_touched"] == ["test/hello.py"]
    assert payload["allow_tool_errors"] is False
    assert payload["allow_safety_blocks"] is False
    assert payload["source_trace_id"] == "read_hello_py"
    assert payload["category"] == "filesystem"
    assert payload["stability"] == "stable"
    assert "tool:read_file" in payload["tags"]
    assert payload["purpose"].startswith("Protect filesystem behavior")

    candidate_payload = json.loads(candidate_case.read_text(encoding="utf-8"))
    assert candidate_payload["category"] == "web"
    assert candidate_payload["stability"] == "candidate"
    assert "network" in candidate_payload["tags"]

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    decisions = {item["trace_id"]: item["decision"] for item in report["items"]}
    assert decisions == {
        "chat_only": "discard",
        "read_hello_py": "regression",
        "web_weather": "candidate",
    }
