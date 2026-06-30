from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from zingclaw.gateway import GatewayEvent
from zingclaw.session import SessionManager
from zingclaw.webui.ws_session import WebSocketSessionState, route_websocket_frame


class FakeSubscription:
    def __init__(self, session_key: str, channel: str, chat_id: str) -> None:
        self.session_key = session_key
        self.channel = channel
        self.chat_id = chat_id
        self.queue: asyncio.Queue[GatewayEvent] = asyncio.Queue()

    async def next(self) -> GatewayEvent:
        return await self.queue.get()

    def close(self) -> None:
        return None


class FakeRuntime:
    def __init__(self, sessions_dir) -> None:
        self.loop = SimpleNamespace(sessions=SessionManager(sessions_dir))
        self.subscriptions: list[FakeSubscription] = []
        self.calls: list[dict[str, object]] = []

    def subscribe(self, *, session_key: str, channel: str, chat_id: str) -> FakeSubscription:
        subscription = FakeSubscription(session_key, channel, chat_id)
        self.subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: FakeSubscription) -> None:
        if subscription in self.subscriptions:
            self.subscriptions.remove(subscription)

    async def submit_user_message(self, **kwargs):
        self.calls.append(dict(kwargs))
        return str(kwargs["message_id"])


@pytest.mark.asyncio
async def test_ws_session_routes_management_frames_and_switches_state(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path / "sessions")
    existing = runtime.loop.sessions.get_or_create("websocket:one")
    existing.set_title("One")
    existing.add_message("user", "hello")
    runtime.loop.sessions.save(existing)

    state = WebSocketSessionState.open(runtime, model_name="kimi-k2.5", requested_chat_id="demo")

    listed = await route_websocket_frame(state, {"type": "list_sessions"})
    detailed = await route_websocket_frame(state, {"type": "get_session", "session_key": "websocket:one"})
    created = await route_websocket_frame(
        state,
        {"type": "create_session", "key": "websocket:new", "title": "Fresh"},
    )
    switched = await route_websocket_frame(state, {"type": "switch_session", "session_key": "websocket:new"})

    assert listed[0]["type"] == "session_list"
    assert listed[0]["sessions"][0]["key"] == "websocket:one"
    assert detailed[0]["type"] == "session_detail"
    assert detailed[0]["messages"][0]["content"] == "hello"
    assert created[0]["type"] == "session_created"
    assert created[0]["session"]["title"] == "Fresh"
    assert switched[0]["type"] == "session_switched"
    assert switched[1] == {
        "type": "ready",
        "chat_id": "new",
        "session_key": "websocket:new",
        "model": "kimi-k2.5",
    }
    assert state.session_key == "websocket:new"
    assert state.chat_id == "new"


@pytest.mark.asyncio
async def test_ws_session_routes_user_message_and_validation_errors(tmp_path) -> None:
    runtime = FakeRuntime(tmp_path / "sessions")
    state = WebSocketSessionState.open(runtime, model_name="kimi-k2.5", requested_chat_id="demo")

    accepted = await route_websocket_frame(
        state,
        {"type": "user_message", "text": "hello", "metadata": {"origin": "web"}, "message_id": "m-1"},
    )
    bad_metadata = await route_websocket_frame(
        state,
        {"type": "user_message", "text": "hello", "metadata": "oops"},
    )
    unsupported = await route_websocket_frame(state, {"type": "unknown"})

    assert accepted == [
        {
            "type": "accepted",
            "message_id": "m-1",
            "chat_id": "demo",
            "session_key": "websocket:demo",
        }
    ]
    assert runtime.calls[0]["session_key"] == "websocket:demo"
    assert bad_metadata == [{"type": "error", "message": "user_message.metadata must be an object"}]
    assert unsupported == [{"type": "error", "message": "unsupported websocket frame type: unknown"}]
