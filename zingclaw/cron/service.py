"""Background cron service for scheduled reminders and tasks."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter

from zingclaw.cron.types import CronJob, CronJobState, CronSchedule
from zingclaw.session import SessionManager


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_timezone_name() -> str:
    tzinfo = datetime.now().astimezone().tzinfo
    key = getattr(tzinfo, "key", None)
    return str(key or "UTC")


def _zoneinfo(tz_name: str):
    if str(tz_name).strip().upper() == "UTC":
        return timezone.utc
    return ZoneInfo(tz_name)


class CronService:
    """Persist and execute simple scheduled jobs in a background thread."""

    def __init__(
        self,
        store_path: Path,
        sessions: SessionManager,
        *,
        default_timezone: str | None = None,
        poll_interval_s: float = 0.2,
    ) -> None:
        self.store_path = Path(store_path).expanduser().resolve()
        self.sessions = sessions
        self.default_timezone = default_timezone or _default_timezone_name()
        self.poll_interval_s = max(0.05, float(poll_interval_s))
        self._lock = threading.RLock()
        self._jobs: dict[str, CronJob] = {}
        self._notifications: dict[str, list[str]] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        self.start()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="zingclaw-cron",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, wait: bool = True, timeout: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if wait and thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))

    def add_job(
        self,
        *,
        name: str,
        schedule: CronSchedule,
        message: str,
        session_key: str,
        origin_channel: str,
        origin_chat_id: str,
        origin_metadata: dict[str, Any] | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        now = _now_ms()
        schedule = replace(schedule)
        state = CronJobState(next_run_at_ms=self._compute_next_run(schedule, now))
        job = CronJob(
            id=uuid.uuid4().hex[:8],
            name=name,
            message=message,
            session_key=session_key,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            origin_metadata=dict(origin_metadata or {}),
            schedule=schedule,
            state=state,
            delete_after_run=delete_after_run,
            enabled=True,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._save_locked()
        return job

    def list_jobs(self) -> list[CronJob]:
        with self._lock:
            jobs = [CronJob.from_dict(job.to_dict()) for job in self._jobs.values()]
        jobs.sort(key=lambda job: (job.state.next_run_at_ms or 0, job.name, job.id))
        return jobs

    def get_job(self, job_id: str) -> CronJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else CronJob.from_dict(job.to_dict())

    def remove_job(self, job_id: str) -> bool:
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
            if existed:
                self._save_locked()
            return existed

    def drain_notifications(self, session_key: str) -> list[str]:
        with self._lock:
            return list(self._notifications.pop(session_key, []))

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self.poll_interval_s):
            self._tick()

    def _tick(self) -> None:
        now = _now_ms()
        due: list[CronJob] = []
        with self._lock:
            changed = False
            for job in list(self._jobs.values()):
                if not job.enabled:
                    continue
                next_run = job.state.next_run_at_ms
                if next_run is None:
                    next_run = self._compute_next_run(job.schedule, now)
                    job.state.next_run_at_ms = next_run
                    changed = True
                if next_run is None or next_run > now:
                    continue

                due.append(CronJob.from_dict(job.to_dict()))
                job.state.last_run_at_ms = now
                job.state.last_status = "triggered"
                job.state.last_error = None
                if job.delete_after_run:
                    self._jobs.pop(job.id, None)
                else:
                    job.state.next_run_at_ms = self._compute_next_run(job.schedule, now + 1)
                changed = True

            if changed:
                self._save_locked()

        for job in due:
            self._deliver(job)

    def _deliver(self, job: CronJob) -> None:
        content = self._render_notification(job)
        with self._lock:
            session = self.sessions.get_or_create(job.session_key)
            session.add_message(
                "assistant",
                content,
                injected_event="cron_result",
                cron_job_id=job.id,
                cron_job_name=job.name,
                source="cron",
                channel=job.origin_channel,
                chat_id=job.origin_chat_id,
            )
            self.sessions.save(session)
            self._notifications.setdefault(job.session_key, []).append(content)

    def _render_notification(self, job: CronJob) -> str:
        header = f"[Scheduled Cron Job '{job.name}' triggered]"
        return f"{header}\n\nTask:\n{job.message}"

    def _compute_next_run(self, schedule: CronSchedule, now_ms: int) -> int | None:
        if schedule.kind == "every":
            every_ms = int(schedule.every_ms or 0)
            return None if every_ms <= 0 else now_ms + every_ms
        if schedule.kind == "at":
            at_ms = int(schedule.at_ms or 0)
            return at_ms if at_ms > now_ms else None
        if schedule.kind == "cron":
            expr = str(schedule.expr or "").strip()
            if not expr:
                return None
            tz_name = schedule.tz or self.default_timezone
            base = datetime.fromtimestamp(now_ms / 1000, tz=_zoneinfo(tz_name))
            return int(croniter(expr, base).get_next(float) * 1000)
        return None

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception:
            return
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            return
        now = _now_ms()
        loaded: dict[str, CronJob] = {}
        for raw in jobs:
            if not isinstance(raw, dict):
                continue
            try:
                job = CronJob.from_dict(raw)
            except Exception:
                continue
            if not job.id or not job.name or not job.session_key:
                continue
            if job.state.next_run_at_ms is None and job.enabled:
                job.state.next_run_at_ms = self._compute_next_run(job.schedule, now)
            loaded[job.id] = job
        with self._lock:
            self._jobs = loaded
            self._save_locked()

    def _save_locked(self) -> None:
        payload = {"jobs": [job.to_dict() for job in self._jobs.values()]}
        self.store_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
