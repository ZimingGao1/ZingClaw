from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from zingclaw.api import create_app
from zingclaw.bus import OutboundMessage, RuntimeEventBus, TurnRunStatusChanged, build_runtime_event_context
from zingclaw.config import Settings
from zingclaw.gateway import GatewayRuntime
from zingclaw.session import SessionManager
from zingclaw.tools import MCPServerStatus


class FakeApiLoop:
    def __init__(self, sessions_dir) -> None:
        self.bus = None
        self._running = False
        self.runtime_events = RuntimeEventBus()
        self.closed = False
        self.sessions = SessionManager(sessions_dir)
        self.reload_count = 0

    def bind_bus(self, bus) -> None:
        self.bus = bus

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                inbound = await asyncio.wait_for(self.bus.consume_inbound(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            context = build_runtime_event_context(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                session_key=inbound.session_key,
                metadata=dict(inbound.metadata),
            )
            await self.runtime_events.publish(
                TurnRunStatusChanged(context=context, status="calling_model", iteration=1)
            )
            if inbound.metadata.get("_wants_stream"):
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        session_key=inbound.session_key,
                        text="assistant:",
                        reply_to=inbound.message_id,
                        final=False,
                        metadata={"_stream_delta": True},
                    )
                )
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        session_key=inbound.session_key,
                        text="hello",
                        reply_to=inbound.message_id,
                        final=False,
                        metadata={"_stream_delta": True},
                    )
                )
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        session_key=inbound.session_key,
                        text="",
                        reply_to=inbound.message_id,
                        final=True,
                        metadata={"stop_reason": "completed", "_streamed": True},
                    )
                )
                continue

            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    session_key=inbound.session_key,
                    text="assistant:hello",
                    reply_to=inbound.message_id,
                    final=True,
                    metadata={"stop_reason": "completed", "usage": {"total_tokens": 12}},
                )
            )

    def stop(self) -> None:
        self._running = False

    async def aclose(self) -> None:
        self.closed = True

    async def get_mcp_status(self, *, connect_missing: bool = True):
        connected = connect_missing or self.reload_count > 0
        return [
            MCPServerStatus(
                name="filesystem",
                transport="stdio",
                connected=connected,
                tool_prefix="mcp_filesystem_",
                target="python -m demo_mcp",
                tool_count=3,
                resource_count=1,
                prompt_count=0,
                capability_count=4,
                capabilities=["tools", "resources"],
                error=None if connected else "not connected yet",
            )
        ]

    async def reload_mcp(self):
        self.reload_count += 1
        return {"ok": True, "message": "MCP config reloaded without restarting ZingClaw."}


@pytest.mark.asyncio
async def test_chat_completions_returns_openai_compatible_json(tmp_path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    runtime = GatewayRuntime(FakeApiLoop(tmp_path / "sessions"))
    app = create_app(runtime, settings, request_timeout_s=2.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        response = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        data = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "assistant:hello"
    assert data["usage"]["total_tokens"] == 12


@pytest.mark.asyncio
async def test_webui_shell_routes_serve_index_and_assets(tmp_path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    runtime = GatewayRuntime(FakeApiLoop(tmp_path / "sessions"))
    app = create_app(runtime, settings, request_timeout_s=2.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        index_response = await client.get("/")
        index_html = await index_response.text()
        asset_response = await client.get("/webui/app.js")
        asset_js = await asset_response.text()
        renderer_response = await client.get("/webui/renderer.js")
        renderer_js = await renderer_response.text()
        view_model_response = await client.get("/webui/view-model.js")
        view_model_js = await view_model_response.text()
        store_response = await client.get("/webui/store.js")
        store_js = await store_response.text()
        protocol_response = await client.get("/webui/protocol.js")
        protocol_js = await protocol_response.text()
        transcript_response = await client.get("/webui/transcript-state.js")
        transcript_js = await transcript_response.text()
        css_response = await client.get("/webui/styles.css")
        css_text = await css_response.text()
    finally:
        await client.close()

    assert index_response.status == 200
    assert index_response.headers["Cache-Control"].startswith("no-store")
    assert "<title>ZingClaw Control Room</title>" in index_html
    assert 'id="transcript-overlay"' in index_html
    assert 'id="sessions-list"' in index_html
    assert 'id="session-search-input"' in index_html
    assert 'id="session-sort-select"' in index_html
    assert 'id="mcp-list"' in index_html
    assert 'id="stage-badge"' in index_html
    assert 'id="tool-strip"' in index_html
    assert 'id="transcript-expand-btn"' in index_html
    assert 'id="runtime-filter-select"' in index_html
    assert 'id="clear-runtime-btn"' in index_html
    assert 'id="reconnect-btn"' in index_html
    assert asset_response.status == 200
    assert asset_response.headers["Cache-Control"].startswith("no-store")
    assert renderer_response.status == 200
    assert view_model_response.status == 200
    assert store_response.status == 200
    assert protocol_response.status == 200
    assert transcript_response.status == 200
    assert css_response.status == 200
    assert "async function bootstrap()" in asset_js
    assert 'new WebSocket(wsUrl(sessionKey))' in asset_js
    assert "let transcriptExpanded = false;" in asset_js
    assert 'document.body.classList.toggle("transcript-expanded"' in asset_js
    assert 'els.transcriptOverlay.hidden = !transcriptExpanded;' in asset_js
    assert 'import { createAppRenderer } from "/webui/renderer.js"' in asset_js
    assert 'import { createUiStore } from "/webui/store.js"' in asset_js
    assert "renderer.render(currentState())" in asset_js
    assert 'scheduleReconnect(sessionKey' in asset_js
    assert 'type: "sessions/search"' in asset_js
    assert 'type: "runtime_feed/filter"' in asset_js
    assert "export function createAppRenderer(els, hooks = {})" in renderer_js
    assert 'from "/webui/view-model.js"' in renderer_js
    assert "export function buildAppViewModel(state)" in view_model_js
    assert "runtimeFeedFilter" in view_model_js
    assert "sessionSearchQuery" in view_model_js
    assert "export function reduceUiState(currentState, action = {})" in store_js
    assert "export function createUiStore(overrides = {})" in store_js
    assert "dispatch(action)" in store_js
    assert "dispatchMany(actions)" in store_js
    assert "runtimeFeedFilter" in store_js
    assert 'case "sessions/search":' in store_js
    assert 'from "/webui/transcript-state.js"' in store_js
    assert "export function reduceWebSocketFrame(frame)" in protocol_js
    assert "export function reduceGatewayEvent(event)" in protocol_js
    assert 'kind: "status"' in protocol_js
    assert 'kind: "tool"' in protocol_js
    assert "export function appendAssistantDelta(state, text)" in transcript_js
    assert "export function finalizeAssistant(state, text = \"\", streamed = false)" in transcript_js
    assert "overflow-y: auto;" in css_text
    assert "overscroll-behavior: contain;" in css_text
    assert ".transcript-overlay" in css_text
    assert "body.transcript-expanded .chat-panel" in css_text


@pytest.mark.asyncio
async def test_chat_completions_streams_sse_chunks(tmp_path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    runtime = GatewayRuntime(FakeApiLoop(tmp_path / "sessions"))
    app = create_app(runtime, settings, request_timeout_s=2.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        response = await client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}], "stream": True},
        )
        text = await response.text()
    finally:
        await client.close()

    assert response.status == 200
    assert "data: " in text
    assert "\"assistant:\"" in text
    assert "\"hello\"" in text
    assert "[DONE]" in text


@pytest.mark.asyncio
async def test_runtime_events_endpoint_emits_normalized_gateway_event(tmp_path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    runtime = GatewayRuntime(FakeApiLoop(tmp_path / "sessions"))
    app = create_app(runtime, settings, request_timeout_s=2.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        response = await client.get("/runtime/events?session_key=demo&channel=api")
        await runtime.submit_user_message(text="hello", session_key="demo", channel="api", chat_id="demo")
        line = await response.content.readline()
        payload = json.loads(line.decode("utf-8").removeprefix("data: ").strip())
    finally:
        response.close()
        await client.close()

    assert payload["kind"] == "runtime"
    assert payload["event_type"] == "turn_run_status_changed"
    assert payload["payload"]["status"] == "calling_model"
    assert payload["session_key"] == "demo"


@pytest.mark.asyncio
async def test_websocket_session_streams_ready_accept_runtime_and_outbound_frames(tmp_path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    runtime = GatewayRuntime(FakeApiLoop(tmp_path / "sessions"))
    app = create_app(runtime, settings, request_timeout_s=2.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        ws = await client.ws_connect("/ws?chat_id=demo")
        ready = await ws.receive_json()
        await ws.send_json({"type": "user_message", "text": "hello"})
        accepted = await ws.receive_json()
        runtime_event = await ws.receive_json()
        outbound_delta_a = await ws.receive_json()
        outbound_delta_b = await ws.receive_json()
        outbound_final = await ws.receive_json()
        await ws.close()
    finally:
        await client.close()

    assert ready == {
        "type": "ready",
        "chat_id": "demo",
        "session_key": "websocket:demo",
        "model": settings.model_name,
    }
    assert accepted["type"] == "accepted"
    assert accepted["chat_id"] == "demo"
    assert accepted["session_key"] == "websocket:demo"

    assert runtime_event["type"] == "event"
    assert runtime_event["event"]["kind"] == "runtime"
    assert runtime_event["event"]["event_type"] == "turn_run_status_changed"
    assert runtime_event["event"]["payload"]["status"] == "calling_model"

    assert outbound_delta_a["event"]["kind"] == "outbound"
    assert outbound_delta_a["event"]["payload"]["text"] == "assistant:"
    assert outbound_delta_a["event"]["payload"]["metadata"]["_stream_delta"] is True

    assert outbound_delta_b["event"]["kind"] == "outbound"
    assert outbound_delta_b["event"]["payload"]["text"] == "hello"
    assert outbound_delta_b["event"]["payload"]["metadata"]["_stream_delta"] is True

    assert outbound_final["event"]["kind"] == "outbound"
    assert outbound_final["event"]["payload"]["final"] is True
    assert outbound_final["event"]["payload"]["metadata"]["_streamed"] is True


@pytest.mark.asyncio
async def test_websocket_session_rejects_invalid_frames(tmp_path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    runtime = GatewayRuntime(FakeApiLoop(tmp_path / "sessions"))
    app = create_app(runtime, settings, request_timeout_s=2.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        ws = await client.ws_connect("/ws")
        ready = await ws.receive_json()
        await ws.send_str("not json")
        error = await ws.receive_json()
        await ws.send_json({"type": "ping"})
        pong = await ws.receive_json()
        await ws.close()
    finally:
        await client.close()

    assert ready["type"] == "ready"
    assert error == {"type": "error", "message": "invalid JSON frame"}
    assert pong == {"type": "pong"}


@pytest.mark.asyncio
async def test_websocket_session_supports_session_management_actions(tmp_path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    fake_loop = FakeApiLoop(tmp_path / "sessions")
    existing = fake_loop.sessions.get_or_create("websocket:one")
    existing.set_title("One")
    existing.add_message("user", "hello one")
    existing.add_message("assistant", "reply one")
    fake_loop.sessions.save(existing)

    another = fake_loop.sessions.get_or_create("websocket:two")
    another.set_title("Two")
    another.add_message("user", "hello two")
    fake_loop.sessions.save(another)

    runtime = GatewayRuntime(fake_loop)
    app = create_app(runtime, settings, request_timeout_s=2.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        ws = await client.ws_connect("/ws?chat_id=demo")
        ready = await ws.receive_json()

        await ws.send_json({"type": "list_sessions"})
        session_list = await ws.receive_json()

        await ws.send_json({"type": "get_session", "session_key": "websocket:one"})
        session_detail = await ws.receive_json()

        await ws.send_json({"type": "rename_session", "session_key": "websocket:one", "title": "One Renamed"})
        renamed = await ws.receive_json()

        await ws.send_json({"type": "clear_session", "session_key": "websocket:one"})
        cleared = await ws.receive_json()

        await ws.send_json({"type": "create_session", "key": "websocket:new", "title": "Fresh"})
        created = await ws.receive_json()

        await ws.send_json({"type": "switch_session", "session_key": "websocket:new"})
        switched = await ws.receive_json()
        ready_after_switch = await ws.receive_json()

        await ws.send_json({"type": "delete_session", "session_key": "websocket:two"})
        deleted = await ws.receive_json()

        await ws.close()
    finally:
        await client.close()

    assert ready["type"] == "ready"

    assert session_list["type"] == "session_list"
    assert {item["key"] for item in session_list["sessions"]} >= {"websocket:one", "websocket:two"}

    assert session_detail["type"] == "session_detail"
    assert session_detail["session"]["key"] == "websocket:one"
    assert [item["content"] for item in session_detail["messages"]] == ["hello one", "reply one"]

    assert renamed["type"] == "session_renamed"
    assert renamed["session"]["title"] == "One Renamed"

    assert cleared["type"] == "session_cleared"
    assert cleared["session"]["key"] == "websocket:one"
    assert cleared["session"]["message_count"] == 0

    assert created["type"] == "session_created"
    assert created["session"]["key"] == "websocket:new"
    assert created["session"]["title"] == "Fresh"

    assert switched["type"] == "session_switched"
    assert switched["session"]["key"] == "websocket:new"
    assert ready_after_switch == {
        "type": "ready",
        "chat_id": "new",
        "session_key": "websocket:new",
        "model": settings.model_name,
    }

    assert deleted == {"type": "session_deleted", "session_key": "websocket:two"}


@pytest.mark.asyncio
async def test_session_http_endpoints_return_list_detail_and_messages(tmp_path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    fake_loop = FakeApiLoop(tmp_path / "sessions")
    session = fake_loop.sessions.get_or_create("websocket:demo")
    session.set_title("Demo Chat")
    session.add_message("user", "hello from session")
    session.add_message("assistant", "hello back")
    fake_loop.sessions.save(session)

    runtime = GatewayRuntime(fake_loop)
    app = create_app(runtime, settings, request_timeout_s=2.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        list_response = await client.get("/api/sessions")
        list_data = await list_response.json()
        detail_response = await client.get("/api/sessions/websocket:demo")
        detail_data = await detail_response.json()
        messages_response = await client.get("/api/sessions/websocket:demo/messages")
        messages_data = await messages_response.json()
    finally:
        await client.close()

    assert list_response.status == 200
    assert list_data["sessions"][0]["key"] == "websocket:demo"
    assert list_data["sessions"][0]["title"] == "Demo Chat"
    assert list_data["sessions"][0]["preview"] == "hello from session"

    assert detail_response.status == 200
    assert detail_data["key"] == "websocket:demo"
    assert detail_data["title"] == "Demo Chat"
    assert detail_data["message_count"] == 2

    assert messages_response.status == 200
    assert messages_data["key"] == "websocket:demo"
    assert [item["content"] for item in messages_data["messages"]] == [
        "hello from session",
        "hello back",
    ]


@pytest.mark.asyncio
async def test_session_http_endpoints_support_create_rename_clear_and_delete(tmp_path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    fake_loop = FakeApiLoop(tmp_path / "sessions")
    runtime = GatewayRuntime(fake_loop)
    app = create_app(runtime, settings, request_timeout_s=2.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        create_response = await client.post("/api/sessions", json={"key": "websocket:new", "title": "Fresh"})
        created = await create_response.json()

        session = fake_loop.sessions.get_or_create("websocket:new")
        session.add_message("user", "draft roadmap")
        fake_loop.sessions.save(session)

        rename_response = await client.post("/api/sessions/websocket:new/rename", json={"title": "Roadmap"})
        renamed = await rename_response.json()

        clear_response = await client.post("/api/sessions/websocket:new/clear")
        cleared = await clear_response.json()

        delete_response = await client.delete("/api/sessions/websocket:new")
        deleted = await delete_response.json()

        missing_response = await client.get("/api/sessions/websocket:new")
        missing = await missing_response.json()
    finally:
        await client.close()

    assert create_response.status == 201
    assert created["key"] == "websocket:new"
    assert created["title"] == "Fresh"

    assert rename_response.status == 200
    assert renamed["title"] == "Roadmap"

    assert clear_response.status == 200
    assert cleared["key"] == "websocket:new"
    assert cleared["message_count"] == 0

    assert delete_response.status == 200
    assert deleted == {"ok": True, "deleted": "websocket:new"}

    assert missing_response.status == 404
    assert missing["error"]["message"] == "session not found"


@pytest.mark.asyncio
async def test_mcp_http_endpoints_return_status_and_reload_updates_state(tmp_path) -> None:
    settings = Settings(_env_file=None, home_dir=tmp_path)
    fake_loop = FakeApiLoop(tmp_path / "sessions")
    runtime = GatewayRuntime(fake_loop)
    app = create_app(runtime, settings, request_timeout_s=2.0)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        status_response = await client.get("/api/mcp/status")
        status_payload = await status_response.json()
        reload_response = await client.post("/api/mcp/reload", json={})
        reload_payload = await reload_response.json()
    finally:
        await client.close()

    assert status_response.status == 200
    assert status_payload["servers"][0]["name"] == "filesystem"
    assert status_payload["servers"][0]["connected"] is True
    assert status_payload["servers"][0]["tool_prefix"] == "mcp_filesystem_"

    assert reload_response.status == 200
    assert reload_payload["ok"] is True
    assert reload_payload["message"] == "MCP config reloaded without restarting ZingClaw."
    assert reload_payload["servers"][0]["connected"] is True
