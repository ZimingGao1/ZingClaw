"""Background subagent execution for spawn."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from zingclaw.agent.context import ContextBuilder
from zingclaw.agent.runner import AgentRunSpec, AgentRunner
from zingclaw.config import Settings
from zingclaw.providers import create_provider
from zingclaw.session import SessionManager
from zingclaw.tools import ToolContext, ToolLoader, ToolRegistry
from zingclaw.utils import render_template


@dataclass(slots=True)
class SubagentStatus:
    """Current state of one spawned background subagent."""

    task_id: str
    label: str
    task: str
    session_key: str
    started_at: float
    phase: str = "queued"
    stop_reason: str | None = None
    error: str | None = None
    result_preview: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cancel_requested: bool = False


@dataclass(slots=True)
class _SubagentControl:
    """Thread/loop handles for cancellation and observation."""

    thread: threading.Thread
    loop: asyncio.AbstractEventLoop | None = None
    root_task: asyncio.Task[Any] | None = None


class SubagentManager:
    """Run background spawned tasks in isolated subagent threads."""

    def __init__(
        self,
        settings: Settings,
        sessions: SessionManager,
        *,
        provider_factory: Any | None = None,
        max_concurrent_subagents: int | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.provider_factory = provider_factory or (lambda: create_provider(settings))
        self.max_concurrent_subagents = max(
            1,
            int(
                max_concurrent_subagents
                if max_concurrent_subagents is not None
                else settings.agent.max_concurrent_subagents
            ),
        )
        self._context_builder = ContextBuilder(
            settings,
            sessions,
            workspace=settings.workspace_dir,
        )
        self._lock = threading.RLock()
        self._running_threads: dict[str, _SubagentControl] = {}
        self._statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}
        self._notifications: dict[str, list[str]] = {}

    async def spawn(
        self,
        *,
        task: str,
        label: str | None = None,
        session_key: str,
        origin_channel: str | None = None,
        origin_chat_id: str | None = None,
        origin_message_id: str | None = None,
        temperature: float | None = None,
    ) -> str:
        del origin_channel, origin_chat_id, origin_message_id
        self._cleanup_finished()

        with self._lock:
            running = len(self._running_threads)
            limit = self.max_concurrent_subagents
            if running >= limit:
                return (
                    f"Cannot spawn subagent: concurrency limit reached ({running}/{limit} running). "
                    "Wait for a running subagent to complete before spawning a new one."
                )

            task_id = uuid.uuid4().hex[:8]
            display_label = self._display_label(task, label=label)
            status = SubagentStatus(
                task_id=task_id,
                label=display_label,
                task=task,
                session_key=session_key,
                started_at=time.monotonic(),
                phase="starting",
            )
            self._statuses[task_id] = status
            self._session_tasks.setdefault(session_key, set()).add(task_id)
            thread = threading.Thread(
                target=self._thread_main,
                args=(task_id, session_key, task, display_label, temperature),
                name=f"zingclaw-subagent-{task_id}",
                daemon=True,
            )
            self._running_threads[task_id] = _SubagentControl(thread=thread)
            thread.start()

        return (
            f"Subagent [{display_label}] started (id: {task_id}). "
            "It will continue in the background and the result will be added to this session."
        )

    def get_running_count(self) -> int:
        self._cleanup_finished()
        with self._lock:
            return len(self._running_threads)

    def get_running_count_by_session(self, session_key: str) -> int:
        self._cleanup_finished()
        with self._lock:
            task_ids = self._session_tasks.get(session_key, set())
            return sum(1 for task_id in task_ids if task_id in self._running_threads)

    def list_statuses(self, *, session_key: str | None = None) -> list[dict[str, Any]]:
        self._cleanup_finished()
        with self._lock:
            items = list(self._statuses.values())
            running_ids = set(self._running_threads)

        if session_key:
            items = [item for item in items if item.session_key == session_key]

        items.sort(key=lambda item: item.started_at, reverse=True)
        now = time.monotonic()
        return [
            {
                "task_id": item.task_id,
                "label": item.label,
                "task": item.task,
                "session_key": item.session_key,
                "phase": item.phase,
                "running": item.task_id in running_ids,
                "cancel_requested": item.cancel_requested,
                "elapsed_s": round(max(0.0, now - item.started_at), 3),
                "stop_reason": item.stop_reason,
                "error": item.error,
                "result_preview": item.result_preview,
                "usage": dict(item.usage),
            }
            for item in items
        ]

    async def cancel_task(
        self,
        task_id: str,
        *,
        session_key: str | None = None,
        wait_timeout: float = 5.0,
    ) -> str:
        self._cleanup_finished()
        with self._lock:
            status = self._statuses.get(task_id)
            control = self._running_threads.get(task_id)
            if status is None:
                return f"Error: unknown subagent task_id '{task_id}'"
            if session_key and status.session_key != session_key:
                return f"Error: subagent '{task_id}' does not belong to session '{session_key}'"
            if control is None:
                return f"Subagent [{status.label}] is not running."
            status.cancel_requested = True
            loop = control.loop
            root_task = control.root_task
            thread = control.thread

        if loop is not None and root_task is not None:
            loop.call_soon_threadsafe(root_task.cancel)

        deadline = time.monotonic() + max(0.0, wait_timeout)
        while thread.is_alive() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
            self._cleanup_finished()

        if thread.is_alive():
            return f"Cancellation requested for subagent [{status.label}] (id: {task_id})."
        return f"Cancelled subagent [{status.label}] (id: {task_id})."

    def drain_notifications(self, session_key: str) -> list[str]:
        with self._lock:
            items = list(self._notifications.pop(session_key, []))
        return items

    def wait_for_all(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else (time.monotonic() + max(0.0, timeout))
        while True:
            self._cleanup_finished()
            with self._lock:
                controls = list(self._running_threads.values())
            if not controls:
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def shutdown(self, *, wait: bool = False, timeout: float | None = None) -> None:
        if wait:
            self.wait_for_all(timeout=timeout)
        self._cleanup_finished()

    def _thread_main(
        self,
        task_id: str,
        session_key: str,
        task: str,
        label: str,
        temperature: float | None,
    ) -> None:
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            root_task = loop.create_task(
                self._run_subagent_async(
                    task_id=task_id,
                    session_key=session_key,
                    task=task,
                    label=label,
                    temperature=temperature,
                )
            )
            with self._lock:
                control = self._running_threads.get(task_id)
                if control is not None:
                    control.loop = loop
                    control.root_task = root_task
                status = self._statuses.get(task_id)
                should_cancel = bool(status.cancel_requested) if status is not None else False
            if should_cancel:
                root_task.cancel()
            loop.run_until_complete(root_task)
        except asyncio.CancelledError:
            with self._lock:
                status = self._statuses.get(task_id)
                if status is not None:
                    status.phase = "cancelled"
                    status.stop_reason = "cancelled"
                    status.result_preview = "Cancelled before completion."
            self._persist_result(
                session_key=session_key,
                task_id=task_id,
                content=self._render_result(
                    label=label,
                    task=task,
                    status_text="cancelled",
                    result="The background task was cancelled before completion.",
                ),
            )
        except Exception as exc:
            self._persist_result(
                session_key=session_key,
                task_id=task_id,
                content=self._render_result(
                    label=label,
                    task=task,
                    status_text="failed",
                    result=f"Error: {exc}",
                ),
            )
            with self._lock:
                status = self._statuses.get(task_id)
                if status is not None:
                    status.phase = "error"
                    status.error = str(exc)
                    status.result_preview = f"Error: {exc}"
        finally:
            if loop is not None and not loop.is_closed():
                loop.close()
            asyncio.set_event_loop(None)
            with self._lock:
                self._running_threads.pop(task_id, None)
                session_tasks = self._session_tasks.get(session_key)
                if session_tasks is not None:
                    session_tasks.discard(task_id)
                    if not session_tasks:
                        self._session_tasks.pop(session_key, None)

    async def _run_subagent_async(
        self,
        *,
        task_id: str,
        session_key: str,
        task: str,
        label: str,
        temperature: float | None,
    ) -> None:
        with self._lock:
            status = self._statuses[task_id]
            if status.cancel_requested:
                raise asyncio.CancelledError
            status.phase = "running"

        provider = self.provider_factory()
        runner = AgentRunner(provider)
        registry = self._build_tools()
        prompt = self._build_prompt()
        result = await runner.run(
            AgentRunSpec(
                initial_messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": task},
                ],
                tools=registry,
                model=self.settings.model_name,
                max_iterations=self.settings.max_iterations,
                max_tool_result_chars=self.settings.max_tool_result_chars,
                temperature=temperature,
                provider_retry_mode=self.settings.agent.provider_retry_mode,
                session_key=session_key,
                workspace=self.settings.workspace_dir,
                metadata={"_subagent_task_id": task_id},
            )
        )

        final = result.final_content or result.error or "Task completed but no final response was generated."
        status_text = "completed" if result.stop_reason == "completed" else result.stop_reason.replace("_", " ")
        content = self._render_result(
            label=label,
            task=task,
            status_text=status_text,
            result=final,
        )
        self._persist_result(
            session_key=session_key,
            task_id=task_id,
            content=content,
        )

        with self._lock:
            status = self._statuses[task_id]
            status.phase = "done" if result.stop_reason == "completed" else "finished"
            status.stop_reason = result.stop_reason
            status.error = result.error
            status.usage = dict(result.usage)
            status.result_preview = final[:200]

    def _build_tools(self) -> ToolRegistry:
        registry = ToolRegistry()
        ToolLoader().load(
            ToolContext.from_settings(
                self.settings,
                sessions=self.sessions,
                bus=None,
                subagent_manager=None,
            ),
            registry,
            scope="subagent",
        )
        return registry

    def _build_prompt(self) -> str:
        extra = render_template(
            "agent/subagent_system.md",
            workspace_path=str(self.settings.workspace_dir),
        )
        return self._context_builder.build_system_prompt(
            workspace=self.settings.workspace_dir,
            channel="subagent",
            session_key=None,
            include_memory_recent_history=False,
            extra_instructions=extra,
        )

    def _persist_result(
        self,
        *,
        session_key: str,
        task_id: str,
        content: str,
    ) -> None:
        with self._lock:
            session = self.sessions.get_or_create(session_key)
            session.add_message(
                "assistant",
                content,
                injected_event="subagent_result",
                subagent_task_id=task_id,
                source="spawn",
            )
            self.sessions.save(session)
            self._notifications.setdefault(session_key, []).append(content)

    @staticmethod
    def _display_label(task: str, *, label: str | None = None) -> str:
        if label and label.strip():
            return label.strip()
        compact = " ".join(str(task).split()).strip()
        if len(compact) <= 48:
            return compact
        return compact[:45].rstrip() + "..."

    @staticmethod
    def _render_result(
        *,
        label: str,
        task: str,
        status_text: str,
        result: str,
    ) -> str:
        return render_template(
            "agent/subagent_result.md",
            label=label,
            task=task,
            status_text=status_text,
            result=result,
        )

    def _cleanup_finished(self) -> None:
        with self._lock:
            finished = [
                task_id
                for task_id, control in self._running_threads.items()
                if not control.thread.is_alive()
            ]
            for task_id in finished:
                self._running_threads.pop(task_id, None)
