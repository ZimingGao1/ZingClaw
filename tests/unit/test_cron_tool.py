from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from zingclaw.cron import CronService
from zingclaw.session import SessionManager
from zingclaw.tools import RequestContext
from zingclaw.tools.cron import CronTool


def _drain_notice(service: CronService, session_key: str, timeout: float = 3.0) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        notices = service.drain_notifications(session_key)
        if notices:
            return notices
        time.sleep(0.05)
    return []


@pytest.mark.asyncio
async def test_cron_tool_add_list_and_remove_job(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path / "sessions")
    service = CronService(
        tmp_path / "workspace" / "cron" / "jobs.json",
        sessions,
        default_timezone="UTC",
        poll_interval_s=0.05,
    )
    tool = CronTool(service, default_timezone="UTC")
    token = tool.set_context(
        RequestContext(
            session_key="cli:demo",
            channel="cli",
            chat_id="demo",
        )
    )

    try:
        created = await tool.execute(
            action="add",
            name="heartbeat",
            message="Remember to check the logs.",
            every_seconds=60,
        )
        listing = await tool.execute(action="list")
        job_id = listing["jobs"][0]["id"]
        removed = await tool.execute(action="remove", job_id=job_id)
        final_listing = await tool.execute(action="list")
    finally:
        tool.reset_context(token)
        service.stop()

    assert "Created job 'heartbeat'" in created
    assert listing["count"] == 1
    assert listing["jobs"][0]["name"] == "heartbeat"
    assert listing["jobs"][0]["schedule"]["kind"] == "every"
    assert removed == f"Removed job {job_id}"
    assert final_listing == {"count": 0, "jobs": []}


@pytest.mark.asyncio
async def test_cron_tool_triggers_one_time_notification_and_persists_session_message(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path / "sessions")
    service = CronService(
        tmp_path / "workspace" / "cron" / "jobs.json",
        sessions,
        default_timezone="UTC",
        poll_interval_s=0.05,
    )
    tool = CronTool(service, default_timezone="UTC")
    token = tool.set_context(
        RequestContext(
            session_key="cli:demo",
            channel="cli",
            chat_id="demo",
        )
    )

    target = (datetime.now().astimezone() + timedelta(seconds=0.3)).isoformat(timespec="milliseconds")
    try:
        created = await tool.execute(
            action="add",
            name="one-shot",
            message="Time to stretch.",
            at=target,
        )
    finally:
        tool.reset_context(token)

    notices = _drain_notice(service, "cli:demo", timeout=4.0)
    session = sessions.get_or_create("cli:demo")
    jobs_after = service.list_jobs()
    service.stop()

    assert "Created job 'one-shot'" in created
    assert len(notices) == 1
    assert "[Scheduled Cron Job 'one-shot' triggered]" in notices[0]
    assert "Time to stretch." in notices[0]
    assert session.messages[-1]["injected_event"] == "cron_result"
    assert session.messages[-1]["cron_job_name"] == "one-shot"
    assert "Time to stretch." in session.messages[-1]["content"]
    assert jobs_after == []


@pytest.mark.asyncio
async def test_cron_tool_accepts_cron_expression_and_timezone(tmp_path: Path) -> None:
    sessions = SessionManager(tmp_path / "sessions")
    service = CronService(
        tmp_path / "workspace" / "cron" / "jobs.json",
        sessions,
        default_timezone="UTC",
        poll_interval_s=0.05,
    )
    tool = CronTool(service, default_timezone="UTC")
    token = tool.set_context(RequestContext(session_key="cli:demo", channel="cli", chat_id="demo"))

    try:
        created = await tool.execute(
            action="add",
            name="daily",
            message="Daily reminder",
            cron_expr="0 9 * * *",
            tz="UTC",
        )
        listing = await tool.execute(action="list")
    finally:
        tool.reset_context(token)
        service.stop()

    assert "Created job 'daily'" in created
    assert listing["count"] == 1
    assert listing["jobs"][0]["schedule"]["kind"] == "cron"
    assert listing["jobs"][0]["schedule"]["expr"] == "0 9 * * *"
    assert listing["jobs"][0]["schedule"]["tz"] == "UTC"
    assert listing["jobs"][0]["state"]["next_run_at_ms"] is not None
