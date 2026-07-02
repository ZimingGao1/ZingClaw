import { reduceWebSocketFrame, normalizeMessage } from "/webui/protocol.js";
import { createAppRenderer } from "/webui/renderer.js";
import { createUiStore } from "/webui/store.js";

const SESSION_STORAGE_KEY = "zingclaw.webui.currentSessionKey";

const store = createUiStore();
let reconnectTimer = null;
let transcriptExpanded = false;
let transcriptPlaceholder = null;
let lastKnownSessionKey = null;

const els = {
  sessionsList: document.getElementById("sessions-list"),
  sessionCount: document.getElementById("session-count"),
  sessionSearchInput: document.getElementById("session-search-input"),
  sessionSortSelect: document.getElementById("session-sort-select"),
  sessionTitle: document.getElementById("session-title"),
  sessionSubtitle: document.getElementById("session-subtitle"),
  modelLabel: document.getElementById("model-label"),
  runtimeStatus: document.getElementById("runtime-status"),
  statusDot: document.getElementById("status-dot"),
  stageBadge: document.getElementById("stage-badge"),
  stageDetail: document.getElementById("stage-detail"),
  toolCount: document.getElementById("tool-count"),
  toolStrip: document.getElementById("tool-strip"),
  messages: document.getElementById("messages"),
  chatPanel: document.getElementById("chat-panel"),
  runtimeFeed: document.getElementById("runtime-feed"),
  runtimeJumpBtn: document.getElementById("runtime-jump-btn"),
  transcriptExpandBtn: document.getElementById("transcript-expand-btn"),
  transcriptCloseBtn: document.getElementById("transcript-close-btn"),
  transcriptJumpBtn: document.getElementById("transcript-jump-btn"),
  transcriptModal: document.getElementById("transcript-modal"),
  transcriptBackdrop: document.getElementById("transcript-backdrop"),
  transcriptModalMount: document.getElementById("transcript-modal-mount"),
  composerForm: document.getElementById("composer-form"),
  composerInput: document.getElementById("composer-input"),
  composerHint: document.getElementById("composer-hint"),
  sendBtn: document.getElementById("send-btn"),
  newSessionBtn: document.getElementById("new-session-btn"),
  refreshSessionsBtn: document.getElementById("refresh-sessions-btn"),
  renameSessionBtn: document.getElementById("rename-session-btn"),
  clearSessionBtn: document.getElementById("clear-session-btn"),
  deleteSessionBtn: document.getElementById("delete-session-btn"),
  refreshMcpBtn: document.getElementById("refresh-mcp-btn"),
  reconnectBtn: document.getElementById("reconnect-btn"),
  runtimeFilterSelect: document.getElementById("runtime-filter-select"),
  clearRuntimeBtn: document.getElementById("clear-runtime-btn"),
  mcpSummary: document.getElementById("mcp-summary"),
  mcpList: document.getElementById("mcp-list"),
  messageTemplate: document.getElementById("message-template"),
};
const renderer = createAppRenderer(els, { onOpenSession: openSession });

function currentState() {
  return store.getState();
}

function wsUrl(sessionKey = "") {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const query = sessionKey ? `?session_key=${encodeURIComponent(sessionKey)}` : "";
  return `${protocol}//${window.location.host}/ws${query}`;
}

function clearReconnectTimer() {
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function persistSessionKey(sessionKey) {
  try {
    if (sessionKey) {
      window.localStorage.setItem(SESSION_STORAGE_KEY, sessionKey);
    } else {
      window.localStorage.removeItem(SESSION_STORAGE_KEY);
    }
  } catch {}
}

function readPreferredSessionKey() {
  const url = new URL(window.location.href);
  const urlSessionKey = url.searchParams.get("session");
  if (urlSessionKey) {
    return urlSessionKey;
  }
  try {
    return window.localStorage.getItem(SESSION_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function syncSessionUrl(sessionKey) {
  const url = new URL(window.location.href);
  if (sessionKey) {
    url.searchParams.set("session", sessionKey);
  } else {
    url.searchParams.delete("session");
  }
  window.history.replaceState({}, "", url);
}

function setTranscriptExpanded(nextValue) {
  const nextExpanded = Boolean(nextValue);
  if (!els.chatPanel || !els.transcriptModal || !els.transcriptModalMount) {
    transcriptExpanded = false;
    return;
  }
  if (nextExpanded === transcriptExpanded) {
    return;
  }

  transcriptExpanded = nextExpanded;
  document.body.classList.toggle("transcript-expanded", transcriptExpanded);

  if (transcriptExpanded) {
    transcriptPlaceholder = document.createComment("transcript-panel-placeholder");
    els.chatPanel.parentNode?.insertBefore(transcriptPlaceholder, els.chatPanel);
    els.transcriptModalMount.appendChild(els.chatPanel);
    els.transcriptModal.hidden = false;
  } else {
    if (transcriptPlaceholder?.parentNode) {
      transcriptPlaceholder.parentNode.replaceChild(els.chatPanel, transcriptPlaceholder);
    }
    transcriptPlaceholder = null;
    els.transcriptModal.hidden = true;
  }
  if (els.transcriptExpandBtn) {
    els.transcriptExpandBtn.textContent = transcriptExpanded ? "Collapse" : "Expand";
    els.transcriptExpandBtn.setAttribute("aria-pressed", transcriptExpanded ? "true" : "false");
  }
  if (els.transcriptCloseBtn) {
    els.transcriptCloseBtn.hidden = !transcriptExpanded;
  }
}

function scheduleReconnect(sessionKey, delayMs = 1500) {
  clearReconnectTimer();
  if (!sessionKey) {
    return;
  }
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    connectWebSocket(sessionKey).catch((error) => {
      store.dispatch({
        type: "runtime_feed/push",
        text: `error: reconnect failed - ${error.message}`,
        kind: "error",
      });
      scheduleReconnect(sessionKey, 2500);
    });
  }, delayMs);
}

store.subscribe(() => {
  const state = currentState();
  if (state.currentSessionKey && state.currentSessionKey !== lastKnownSessionKey) {
    lastKnownSessionKey = state.currentSessionKey;
    persistSessionKey(state.currentSessionKey);
    syncSessionUrl(state.currentSessionKey);
  }
  renderer.render(state);
});

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      message = payload?.error?.message || message;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}

async function loadSessions() {
  const payload = await requestJson("/api/sessions");
  store.dispatch({ type: "sessions/set", sessions: payload.sessions || [] });
  return currentState().sessions;
}

async function loadSessionMessages(sessionKey) {
  const payload = await requestJson(`/api/sessions/${encodeURIComponent(sessionKey)}/messages`);
  store.dispatch({
    type: "messages/set",
    messages: (payload.messages || []).map(normalizeMessage),
  });
}

async function loadMcpStatus() {
  const payload = await requestJson("/api/mcp/status");
  store.dispatch({ type: "mcp/set", servers: payload.servers || [] });
}

async function reloadMcpStatus() {
  const payload = await requestJson("/api/mcp/reload", {
    method: "POST",
    body: JSON.stringify({}),
  });
  store.dispatchMany([
    { type: "mcp/set", servers: payload.servers || [] },
    { type: "runtime_feed/push", text: payload.message || "mcp reloaded", kind: "system" },
  ]);
}

async function connectWebSocket(sessionKey = "") {
  const state = currentState();
  clearReconnectTimer();
  if (state.ws) {
    state.ws._expectedClose = true;
    state.ws.close();
  }
  store.dispatch({ type: "status/set", status: "connecting", tone: "active" });
  const ws = new WebSocket(wsUrl(sessionKey));
  store.dispatch({ type: "socket/set", ws });

  ws.addEventListener("open", () => {
    clearReconnectTimer();
    store.dispatch({ type: "runtime_feed/push", text: "websocket opened", kind: "system" });
  });

  ws.addEventListener("message", async (messageEvent) => {
    const frame = JSON.parse(messageEvent.data);
    await handleWebSocketFrame(frame);
  });

  ws.addEventListener("close", () => {
    const shouldReconnect = !ws._expectedClose && Boolean(currentState().currentSessionKey);
    store.dispatchMany([
      { type: "status/set", status: "disconnected", tone: "idle" },
      { type: "runtime_feed/push", text: "websocket closed", kind: shouldReconnect ? "error" : "system" },
    ]);
    if (shouldReconnect) {
      store.dispatch({
        type: "runtime_feed/push",
        text: `reconnecting to ${currentState().currentSessionKey}...`,
        kind: "system",
      });
      scheduleReconnect(currentState().currentSessionKey);
    }
  });

  ws.addEventListener("error", () => {
    store.dispatchMany([
      { type: "status/set", status: "connection error", tone: "idle" },
      { type: "runtime_feed/push", text: "websocket error", kind: "error" },
    ]);
  });
}

async function handleWebSocketFrame(frame) {
  const actions = reduceWebSocketFrame(frame);
  for (const action of actions) {
    if (action.type === "connection_ready") {
      store.dispatch({
        type: "connection/ready",
        model: action.model,
        sessionKey: action.sessionKey,
        chatId: action.chatId,
      });
      await loadSessions();
      await loadSessionMessages(action.sessionKey);
      await loadMcpStatus().catch(() => {});
      continue;
    }

    if (action.type === "status") {
      store.dispatch({ type: "status/set", status: action.status, tone: action.tone });
      continue;
    }

    if (action.type === "stage") {
      store.dispatch({ type: "stage/set", stage: action.stage, detail: action.detail });
      continue;
    }

    if (action.type === "runtime_line") {
      store.dispatch({
        type: "runtime_feed/push",
        text: action.text,
        kind: action.kind || "system",
      });
      continue;
    }

    if (action.type === "sessions_loaded") {
      store.dispatch({ type: "sessions/set", sessions: action.sessions || [] });
      continue;
    }

    if (action.type === "session_selected") {
      store.dispatch({ type: "session/summary", session: action.session || null });
      continue;
    }

    if (action.type === "session_header") {
      if (action.session) {
        store.dispatch({ type: "session/summary", session: action.session });
      }
      continue;
    }

    if (action.type === "messages_loaded") {
      store.dispatch({ type: "messages/set", messages: action.messages || [] });
      continue;
    }

    if (action.type === "tool_state") {
      store.dispatch({ type: "tools/upsert", name: action.toolName, toolState: action.toolState });
      if (action.toolState !== "running") {
        window.setTimeout(() => {
          store.dispatch({ type: "tools/remove", name: action.toolName });
        }, 2000);
      }
      continue;
    }

    if (action.type === "assistant_stream_delta") {
      store.dispatch({ type: "transcript/assistant_delta", text: action.text });
      continue;
    }

    if (action.type === "assistant_final") {
      store.dispatch({
        type: "transcript/assistant_final",
        text: action.text,
        streamed: action.streamed,
      });
      continue;
    }

    if (action.type === "refresh_sessions") {
      await loadSessions().catch(() => {});
      continue;
    }

    if (action.type === "session_deleted") {
      if (action.sessionKey === currentState().currentSessionKey) {
        await loadSessions();
        const next = currentState().sessions[0];
        if (next) {
          await openSession(next.key);
        } else {
          await createSession();
        }
      }
    }
  }
}

async function openSession(sessionKey) {
  if (!sessionKey) {
    return;
  }
  store.dispatchMany([{ type: "runtime_feed/clear" }, { type: "transcript/reset" }]);
  const { ws } = currentState();
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "switch_session", session_key: sessionKey }));
    return;
  }
  await connectWebSocket(sessionKey);
}

async function createSession() {
  const created = await requestJson("/api/sessions", {
    method: "POST",
    body: JSON.stringify({}),
  });
  await loadSessions();
  await openSession(created.key);
}

function sendCurrentMessage(text) {
  const trimmed = text.trim();
  const { ws } = currentState();
  if (!trimmed || !ws || ws.readyState !== WebSocket.OPEN) {
    return;
  }
  store.dispatchMany([
    { type: "transcript/user_message", text: trimmed },
    { type: "status/set", status: "sending", tone: "active" },
    {
      type: "stage/set",
      stage: "running",
      detail: "Your request is queued and the agent is starting a new turn.",
    },
  ]);
  ws.send(JSON.stringify({ type: "user_message", text: trimmed, stream: true }));
}

async function renameCurrentSession() {
  const { currentSessionKey, ws } = currentState();
  if (!currentSessionKey) {
    return;
  }
  const nextTitle = window.prompt("Rename session", els.sessionTitle.textContent || "");
  if (!nextTitle || !ws || ws.readyState !== WebSocket.OPEN) {
    return;
  }
  ws.send(JSON.stringify({ type: "rename_session", session_key: currentSessionKey, title: nextTitle }));
}

async function clearCurrentSession() {
  const { currentSessionKey, ws } = currentState();
  if (!currentSessionKey || !ws || ws.readyState !== WebSocket.OPEN) {
    return;
  }
  if (!window.confirm("Clear the current session transcript?")) {
    return;
  }
  ws.send(JSON.stringify({ type: "clear_session", session_key: currentSessionKey }));
}

async function deleteCurrentSession() {
  const { currentSessionKey, ws } = currentState();
  if (!currentSessionKey || !ws || ws.readyState !== WebSocket.OPEN) {
    return;
  }
  if (!window.confirm("Delete this session?")) {
    return;
  }
  ws.send(JSON.stringify({ type: "delete_session", session_key: currentSessionKey }));
}

if (els.composerForm && els.composerInput) {
  els.composerForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = els.composerInput.value;
    if (!text.trim()) {
      return;
    }
    sendCurrentMessage(text);
    els.composerInput.value = "";
    els.composerInput.focus();
  });

  els.composerInput.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      els.composerForm.requestSubmit();
    }
  });
}

els.newSessionBtn?.addEventListener("click", () =>
  createSession().catch((error) =>
    store.dispatch({ type: "runtime_feed/push", text: `error: ${error.message}`, kind: "error" }),
  ),
);
els.refreshSessionsBtn?.addEventListener("click", () =>
  loadSessions().catch((error) =>
    store.dispatch({ type: "runtime_feed/push", text: `error: ${error.message}`, kind: "error" }),
  ),
);
els.refreshMcpBtn?.addEventListener("click", () =>
  reloadMcpStatus().catch((error) =>
    store.dispatch({ type: "runtime_feed/push", text: `error: ${error.message}`, kind: "error" }),
  ),
);
els.reconnectBtn?.addEventListener("click", () => {
  const sessionKey = currentState().currentSessionKey;
  if (!sessionKey) {
    return;
  }
  store.dispatch({ type: "runtime_feed/push", text: `manual reconnect: ${sessionKey}`, kind: "system" });
  connectWebSocket(sessionKey).catch((error) =>
    store.dispatch({ type: "runtime_feed/push", text: `error: ${error.message}`, kind: "error" }),
  );
});
els.clearRuntimeBtn?.addEventListener("click", () => {
  store.dispatch({ type: "runtime_feed/clear" });
});
els.sessionSearchInput?.addEventListener("input", (event) => {
  store.dispatch({ type: "sessions/search", query: event.target.value });
});
els.sessionSortSelect?.addEventListener("change", (event) => {
  store.dispatch({ type: "sessions/sort", sortKey: event.target.value });
});
els.runtimeFilterSelect?.addEventListener("change", (event) => {
  store.dispatch({ type: "runtime_feed/filter", filter: event.target.value });
});
els.transcriptExpandBtn?.addEventListener("click", () => {
  setTranscriptExpanded(!transcriptExpanded);
});
els.transcriptCloseBtn?.addEventListener("click", () => {
  setTranscriptExpanded(false);
});
els.transcriptBackdrop?.addEventListener("click", () => {
  setTranscriptExpanded(false);
});
els.renameSessionBtn?.addEventListener("click", () => renameCurrentSession());
els.clearSessionBtn?.addEventListener("click", () => clearCurrentSession());
els.deleteSessionBtn?.addEventListener("click", () => deleteCurrentSession());

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && transcriptExpanded) {
    setTranscriptExpanded(false);
  }
});

async function bootstrap() {
  renderer.render(currentState());
  try {
    const sessions = await loadSessions();
    await loadMcpStatus();
    const preferredSessionKey = readPreferredSessionKey();
    const first = sessions.find((item) => item.key === preferredSessionKey) || sessions[0];
    if (first) {
      await connectWebSocket(first.key);
      return;
    }
    await createSession();
  } catch (error) {
    store.dispatchMany([
      { type: "status/set", status: "bootstrap failed", tone: "idle" },
      { type: "runtime_feed/push", text: `error: ${error.message}`, kind: "error" },
    ]);
  }
}

bootstrap();
window.setInterval(() => {
  loadSessions().catch(() => {});
  loadMcpStatus().catch(() => {});
}, 15000);
