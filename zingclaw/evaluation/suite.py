"""Batch replay suite runner for regression evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zingclaw.evaluation.replay import ReplayCase, run_replay_case
from zingclaw.evaluation.trace import TraceStore


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class SuiteCaseResult:
    """Result for one replay case inside a suite run."""

    case_name: str
    case_path: str
    passed: bool
    score: int
    category: str = ""
    tags: list[str] = field(default_factory=list)
    purpose: str = ""
    stability: str = ""
    trace_id: str = ""
    trace_dir: str = ""
    failed_checks: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "case_path": self.case_path,
            "passed": self.passed,
            "score": self.score,
            "category": self.category,
            "tags": list(self.tags),
            "purpose": self.purpose,
            "stability": self.stability,
            "trace_id": self.trace_id,
            "trace_dir": self.trace_dir,
            "failed_checks": list(self.failed_checks),
            "error": self.error,
        }


@dataclass(slots=True)
class SuiteRunResult:
    """Summary for one replay suite run."""

    suite_path: Path
    report_path: Path
    readable_report_path: Path
    total: int
    passed: int
    failed: int
    pass_rate: float
    cases: list[SuiteCaseResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": _utcnow(),
            "suite_path": str(self.suite_path),
            "report_path": str(self.report_path),
            "readable_report_path": str(self.readable_report_path),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.pass_rate,
            "cases": [item.to_dict() for item in self.cases],
        }


def discover_replay_cases(suite_path: Path, *, pattern: str = "*.json") -> list[Path]:
    """Discover replay case JSON files from a file or directory."""
    path = Path(suite_path).expanduser().resolve(strict=False)
    if path.is_file():
        return [path]
    if not path.exists():
        raise FileNotFoundError(f"replay suite path does not exist: {path}")
    return sorted(
        item
        for item in path.rglob(pattern)
        if item.is_file() and not item.name.startswith("suite_report") and item.name != "manifest.json"
    )


def _default_report_path(suite_path: Path) -> Path:
    path = Path(suite_path).expanduser().resolve(strict=False)
    if path.is_file():
        return path.parent / "suite_report.json"
    return path / "suite_report.json"


def _failed_check_names(scorecard: Any) -> list[str]:
    failed: list[str] = []
    for check in getattr(scorecard, "checks", []) or []:
        if not bool(getattr(check, "passed", False)):
            name = str(getattr(check, "name", "check"))
            detail = str(getattr(check, "detail", "") or "")
            failed.append(f"{name}: {detail}" if detail else name)
    return failed


def _default_readable_report_path(report_path: Path) -> Path:
    return Path(report_path).with_suffix(".md")


def _status_label(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _escape_table(value: Any) -> str:
    text = str(value or "").replace("\r\n", " ").replace("\n", " ").strip()
    return text.replace("|", "\\|")


def _short_path(value: str) -> str:
    if not value:
        return "-"
    path = Path(value)
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _failures_by_line(item: SuiteCaseResult) -> list[str]:
    if item.error:
        return [item.error]
    return list(item.failed_checks) or ["unknown failure"]


def _write_readable_report(result: SuiteRunResult, *, generated_at: str) -> None:
    failed_cases = [item for item in result.cases if not item.passed]
    lines = [
        "# ZingClaw Eval Suite Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Suite: `{result.suite_path}`",
        f"- Total: `{result.total}`",
        f"- Passed: `{result.passed}`",
        f"- Failed: `{result.failed}`",
        f"- Pass rate: `{result.pass_rate:.2f}%`",
        f"- JSON report: `{result.report_path}`",
        "",
    ]
    if failed_cases:
        lines.extend(["## Failed Cases", ""])
        for item in failed_cases:
            lines.extend(
                [
                    f"### {item.case_name}",
                    "",
                    f"- Score: `{item.score}`",
                    f"- Category: `{item.category or '-'}`",
                    f"- Trace: `{_short_path(item.trace_dir)}`",
                    "- Failed checks:",
                ]
            )
            for failure in _failures_by_line(item):
                lines.append(f"  - {failure}")
            lines.append("")
    else:
        lines.extend(["## Failed Cases", "", "No regressions detected.", ""])

    lines.extend(
        [
            "## Case Details",
            "",
            "| Status | Case | Score | Category | Trace |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for item in result.cases:
        lines.append(
            "| "
            f"{_status_label(item.passed)} | "
            f"{_escape_table(item.case_name)} | "
            f"{item.score} | "
            f"{_escape_table(item.category or '-')} | "
            f"`{_escape_table(_short_path(item.trace_dir))}` |"
        )
    lines.append("")
    lines.extend(["## Case Metadata", ""])
    for item in result.cases:
        lines.extend(
            [
                f"### {item.case_name}",
                "",
                f"- Status: `{_status_label(item.passed)}`",
                f"- Score: `{item.score}`",
                f"- Category: `{item.category or '-'}`",
                f"- Tags: `{', '.join(item.tags) or '-'}`",
                f"- Purpose: {item.purpose or '-'}",
                f"- Case file: `{_short_path(item.case_path)}`",
                f"- Fresh trace: `{_short_path(item.trace_dir)}`",
                "",
            ]
        )
    result.readable_report_path.write_text("\n".join(lines), encoding="utf-8")


async def run_replay_suite(
    loop: Any,
    suite_path: Path,
    store: TraceStore,
    *,
    pattern: str = "*.json",
    report_path: Path | None = None,
    readable_report_path: Path | None = None,
) -> SuiteRunResult:
    """Run every replay case in a suite and write one aggregate report."""
    suite = Path(suite_path).expanduser().resolve(strict=False)
    case_paths = discover_replay_cases(suite, pattern=pattern)
    if not case_paths:
        raise ValueError(f"no replay cases matched {pattern!r} under {suite}")

    case_results: list[SuiteCaseResult] = []
    for case_path in case_paths:
        try:
            case = ReplayCase.from_file(case_path)
            replay_result = await run_replay_case(loop, case, store)
            scorecard = replay_result.scorecard
            case_results.append(
                SuiteCaseResult(
                    case_name=case.name,
                    case_path=str(case_path),
                    passed=scorecard.passed,
                    score=scorecard.score,
                    category=case.category,
                    tags=list(case.tags),
                    purpose=case.purpose,
                    stability=case.stability,
                    trace_id=replay_result.trace_id,
                    trace_dir=str(replay_result.trace_dir),
                    failed_checks=_failed_check_names(scorecard),
                )
            )
        except Exception as exc:
            case_results.append(
                SuiteCaseResult(
                    case_name=case_path.stem,
                    case_path=str(case_path),
                    passed=False,
                    score=0,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    passed = sum(1 for item in case_results if item.passed)
    total = len(case_results)
    failed = total - passed
    pass_rate = round((passed / total) * 100, 2) if total else 0.0
    destination = Path(report_path).expanduser().resolve(strict=False) if report_path else _default_report_path(suite)
    destination.parent.mkdir(parents=True, exist_ok=True)
    readable_destination = (
        Path(readable_report_path).expanduser().resolve(strict=False)
        if readable_report_path
        else _default_readable_report_path(destination)
    )
    readable_destination.parent.mkdir(parents=True, exist_ok=True)
    generated_at = _utcnow()
    result = SuiteRunResult(
        suite_path=suite,
        report_path=destination,
        readable_report_path=readable_destination,
        total=total,
        passed=passed,
        failed=failed,
        pass_rate=pass_rate,
        cases=case_results,
    )
    payload = result.to_dict()
    payload["generated_at"] = generated_at
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_readable_report(result, generated_at=generated_at)
    return result
