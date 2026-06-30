from __future__ import annotations

from types import SimpleNamespace

import pytest

from zingclaw.session import SessionManager
from zingclaw.webui.session_service import SessionNotFoundError, SessionService


class FakeRuntime:
    def __init__(self, sessions_dir) -> None:
        self.loop = SimpleNamespace(sessions=SessionManager(sessions_dir))
        self.calls: list[dict[str, object]] = []

    async def submit_user_message(self, **kwargs):
        self.calls.append(dict(kwargs))
        return str(kwargs["message_id"])


def test_session_service_crud_flow(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path / "sessions")
    service = SessionService(runtime)

    created = service.create_session(key="websocket:demo", title="Demo Chat")
    listed = service.list_sessions()
    detail = service.get_detail("websocket:demo")

    assert created["key"] == "websocket:demo"
    assert created["title"] == "Demo Chat"
    assert listed[0]["key"] == "websocket:demo"
    assert detail == {"session": created, "messages": []}

    runtime.loop.sessions.get_or_create("websocket:demo").add_message("user", "draft plan")
    runtime.loop.sessions.save(runtime.loop.sessions.get_or_create("websocket:demo"))

    renamed = service.rename_session("websocket:demo", "Roadmap")
    cleared = service.clear_session("websocket:demo")

    assert renamed["title"] == "Roadmap"
    assert cleared["message_count"] == 0

    service.delete_session("websocket:demo")
    with pytest.raises(SessionNotFoundError):
        service.get_metadata("websocket:demo")


@pytest.mark.asyncio
async def test_session_service_submits_user_message_with_websocket_defaults(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path / "sessions")
    service = SessionService(runtime)

    message_id = await service.submit_user_message(
        text="hello",
        session_key="websocket:demo",
        chat_id="demo",
        metadata={"origin": "browser"},
        stream=True,
        message_id="msg-1",
    )

    assert message_id == "msg-1"
    assert runtime.calls == [
        {
            "text": "hello",
            "session_key": "websocket:demo",
            "channel": "websocket",
            "chat_id": "demo",
            "metadata": {"origin": "browser"},
            "wants_stream": True,
            "message_id": "msg-1",
        }
    ]
