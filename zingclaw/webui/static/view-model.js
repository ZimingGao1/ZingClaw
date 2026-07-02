function sessionTitle(session) {
  return session?.title || session?.key || "Untitled Session";
}

function formatTime(rawValue) {
  if (!rawValue) {
    return "";
  }
  const date = new Date(rawValue);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function compactText(rawValue, maxChars = 140) {
  const text = String(rawValue || "").replace(/\s+/g, " ").trim();
  if (!text) {
    return "";
  }
  if (text.length <= maxChars) {
    return text;
  }
  return `${text.slice(0, maxChars - 3).trimEnd()}...`;
}

export function buildAppViewModel(state) {
  const session = state.currentSessionSummary || { key: state.currentSessionKey };
  const stage = state.currentStage || "idle";
  const socketReady = Boolean(state.ws && state.ws.readyState === 1);
  const canManageSession = Boolean(state.currentSessionKey);
  const searchQuery = String(state.sessionSearchQuery || "").trim().toLowerCase();
  const sessions = (state.sessions || [])
    .map((item) => ({
      key: item.key,
      title: sessionTitle(item),
      preview: item.preview || item.summary || "No messages yet",
      isActive: item.key === state.currentSessionKey,
      lastActiveLabel: formatTime(item.last_active_at) || "inactive",
      lastActiveAt: item.last_active_at || "",
      messageCount: Number(item.message_count || 0),
    }))
    .filter((item) => {
      if (!searchQuery) {
        return true;
      }
      return `${item.title} ${item.preview} ${item.key}`.toLowerCase().includes(searchQuery);
    });
  const sessionSortKey = state.sessionSortKey || "recent";
  sessions.sort((left, right) => {
    if (sessionSortKey === "title") {
      return left.title.localeCompare(right.title);
    }
    if (sessionSortKey === "messages") {
      return right.messageCount - left.messageCount || right.lastActiveAt.localeCompare(left.lastActiveAt);
    }
    return right.lastActiveAt.localeCompare(left.lastActiveAt);
  });
  const tools = (state.activeTools || []).map((tool) => ({
    name: tool.name || "tool",
    state: tool.state || "running",
  }));
  const mcpServers = (state.mcpServers || []).map((server) => ({
    name: server.name,
    transport: server.transport || "-",
    toolCount: server.tool_count,
    resourceCount: server.resource_count,
    promptCount: server.prompt_count,
    toolNames: Array.isArray(server.tool_names) ? server.tool_names : [],
    resourceNames: Array.isArray(server.resource_names) ? server.resource_names : [],
    promptNames: Array.isArray(server.prompt_names) ? server.prompt_names : [],
    toolPrefix: server.tool_prefix || "-",
    target: server.target || "",
    connected: Boolean(server.connected),
    stateClass: server.connected ? "connected" : "unavailable",
    stateLabel: server.connected ? "connected" : "unavailable",
    error: server.error || "",
  }));

  return {
    status: {
      text: state.runtimeStatus,
      tone: state.runtimeStatusTone,
    },
    header: {
      title: sessionTitle(session),
      subtitle:
        compactText(session?.preview, 140) ||
        compactText(session?.summary, 140) ||
        compactText(state.currentSessionKey, 140) ||
        "No session selected",
      model: `model: ${state.model}`,
    },
    actions: {
      canReconnect: canManageSession,
      canManageSession,
      canSend: socketReady && canManageSession,
    },
    stage: {
      name: stage,
      label: stage.replaceAll("_", " "),
      detail: state.currentStageDetail || "Waiting for the next turn.",
    },
    tools: {
      count: String(tools.length),
      items: tools,
      isEmpty: tools.length === 0,
    },
    runtimeFeed: {
      lines: (state.runtimeFeed || []).filter((item) => {
        const filter = state.runtimeFeedFilter || "all";
        return filter === "all" ? true : item.kind === filter;
      }),
      isEmpty: (state.runtimeFeed || []).filter((item) => {
        const filter = state.runtimeFeedFilter || "all";
        return filter === "all" ? true : item.kind === filter;
      }).length === 0,
      filter: state.runtimeFeedFilter || "all",
    },
    sessions: {
      count: String(sessions.length),
      items: sessions,
      isEmpty: sessions.length === 0,
      searchQuery: state.sessionSearchQuery || "",
      sortKey: sessionSortKey,
    },
    messages: {
      items: (state.messages || []).map((item) => ({
        ...item,
        timeLabel: formatTime(item.timestamp),
      })),
      isEmpty: (state.messages || []).length === 0,
    },
    mcp: {
      summary:
        mcpServers.length === 0
          ? "no configured servers"
          : `${mcpServers.filter((item) => item.connected).length}/${mcpServers.length} connected`,
      items: mcpServers,
      isEmpty: mcpServers.length === 0,
    },
    composer: {
      hint: socketReady
        ? "Streaming replies and runtime progress appear in real time. Press Ctrl+Enter to send."
        : "Connecting to the gateway. Sending will unlock as soon as the session is ready.",
    },
  };
}
