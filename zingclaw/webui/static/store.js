import {
  appendAssistantDelta,
  appendUserMessage,
  finalizeAssistant,
  replaceMessages,
  resetTranscript,
} from "/webui/transcript-state.js";

export const DEFAULT_STATE = {
  model: "-",
  ws: null,
  currentSessionKey: null,
  currentChatId: null,
  currentSessionSummary: null,
  sessions: [],
  messages: [],
  runtimeFeed: [],
  runtimeStatus: "connecting",
  runtimeStatusTone: "active",
  pendingAssistantId: null,
  mcpServers: [],
  currentStage: "idle",
  currentStageDetail: "Waiting for the next turn.",
  activeTools: [],
  sessionSearchQuery: "",
  sessionSortKey: "recent",
  runtimeFeedFilter: "all",
};

function cloneState(state) {
  return {
    ...state,
    currentSessionSummary: state.currentSessionSummary ? { ...state.currentSessionSummary } : null,
    sessions: Array.isArray(state.sessions) ? [...state.sessions] : [],
    messages: Array.isArray(state.messages) ? [...state.messages] : [],
    runtimeFeed: Array.isArray(state.runtimeFeed) ? state.runtimeFeed.map((item) => ({ ...item })) : [],
    mcpServers: Array.isArray(state.mcpServers) ? [...state.mcpServers] : [],
    activeTools: Array.isArray(state.activeTools)
      ? state.activeTools.map((item) => ({ ...item }))
      : [],
  };
}

export function reduceUiState(currentState, action = {}) {
  const next = cloneState(currentState);

  switch (action.type) {
    case "socket/set":
      next.ws = action.ws || null;
      return next;

    case "connection/ready":
      next.model = action.model || next.model;
      next.currentSessionKey = action.sessionKey || next.currentSessionKey;
      next.currentChatId = action.chatId || next.currentChatId;
      if (next.currentSessionKey) {
        next.currentSessionSummary =
          next.currentSessionSummary?.key === next.currentSessionKey
            ? next.currentSessionSummary
            : { key: next.currentSessionKey };
      }
      return next;

    case "session/summary":
      next.currentSessionSummary = action.session ? { ...action.session } : null;
      if (action.session?.key) {
        next.currentSessionKey = action.session.key;
      }
      return next;

    case "sessions/set":
      next.sessions = Array.isArray(action.sessions) ? [...action.sessions] : [];
      {
        const current = next.sessions.find((item) => item.key === next.currentSessionKey);
        if (current) {
          next.currentSessionSummary = { ...current };
        }
      }
      return next;

    case "messages/set":
      replaceMessages(next, action.messages);
      return next;

    case "mcp/set":
      next.mcpServers = Array.isArray(action.servers) ? [...action.servers] : [];
      return next;

    case "status/set":
      next.runtimeStatus = action.status;
      next.runtimeStatusTone = action.tone || "active";
      return next;

    case "stage/set":
      next.currentStage = action.stage || "idle";
      next.currentStageDetail = action.detail || "Waiting for the next turn.";
      return next;

    case "runtime_feed/clear":
      next.runtimeFeed = [];
      return next;

    case "runtime_feed/push":
      if (!action.text) {
        return currentState;
      }
      next.runtimeFeed.push({
        text: action.text,
        kind: action.kind || "system",
      });
      next.runtimeFeed = next.runtimeFeed.slice(-80);
      return next;

    case "runtime_feed/filter":
      next.runtimeFeedFilter = action.filter || "all";
      return next;

    case "transcript/reset":
      resetTranscript(next);
      return next;

    case "transcript/user_message":
      appendUserMessage(next, action.text);
      return next;

    case "transcript/assistant_delta":
      appendAssistantDelta(next, action.text);
      return next;

    case "transcript/assistant_final":
      finalizeAssistant(next, action.text, action.streamed);
      return next;

    case "tools/upsert":
      {
        const existing = next.activeTools.find((item) => item.name === action.name);
        if (existing) {
          existing.state = action.toolState;
        } else {
          next.activeTools.push({ name: action.name, state: action.toolState });
        }
      }
      return next;

    case "tools/remove":
      next.activeTools = next.activeTools.filter((item) => item.name !== action.name);
      return next;

    case "sessions/search":
      next.sessionSearchQuery = action.query || "";
      return next;

    case "sessions/sort":
      next.sessionSortKey = action.sortKey || "recent";
      return next;

    default:
      return currentState;
  }
}

export function createUiStore(overrides = {}) {
  let state = { ...DEFAULT_STATE, ...overrides };
  const listeners = new Set();

  function notify() {
    for (const listener of listeners) {
      listener(state);
    }
  }

  function dispatch(action) {
    const next = reduceUiState(state, action);
    if (next === state) {
      return state;
    }
    state = next;
    notify();
    return state;
  }

  function dispatchMany(actions) {
    let next = state;
    for (const action of actions) {
      next = reduceUiState(next, action);
    }
    if (next === state) {
      return state;
    }
    state = next;
    notify();
    return state;
  }

  return {
    getState() {
      return state;
    },

    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    dispatch,
    dispatchMany,
  };
}
