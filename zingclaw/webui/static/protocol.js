export function normalizeMessage(item = {}) {
  return {
    role: item.role || "assistant",
    content: typeof item.content === "string" ? item.content : JSON.stringify(item.content ?? ""),
    timestamp: item.timestamp || "",
  };
}

export function reduceWebSocketFrame(frame) {
  if (!frame || typeof frame !== "object") {
    return [{ type: "runtime_line", text: "error: invalid websocket frame" }];
  }

  if (frame.type === "ready") {
    return [
      {
        type: "connection_ready",
        model: frame.model || "-",
        sessionKey: frame.session_key || null,
        chatId: frame.chat_id || null,
      },
      { type: "status", status: "ready", tone: "ok" },
      { type: "stage", stage: "idle", detail: "Connected. Waiting for your next request." },
      { type: "runtime_line", text: `ready: ${frame.session_key}`, kind: "system" },
    ];
  }

  if (frame.type === "accepted") {
    return [
      { type: "status", status: "accepted", tone: "active" },
      { type: "runtime_line", text: `accepted message: ${frame.message_id}`, kind: "system" },
    ];
  }

  if (frame.type === "event") {
    return reduceGatewayEvent(frame.event);
  }

  if (frame.type === "session_list") {
    return [{ type: "sessions_loaded", sessions: frame.sessions || [] }];
  }

  if (frame.type === "session_detail") {
    return [
      { type: "session_selected", session: frame.session || {} },
      { type: "messages_loaded", messages: (frame.messages || []).map(normalizeMessage) },
      { type: "refresh_sessions" },
    ];
  }

  if (frame.type === "session_created") {
    return [
      { type: "session_header", session: frame.session || null },
      { type: "runtime_line", text: "session created", kind: "system" },
      { type: "refresh_sessions" },
    ];
  }

  if (frame.type === "session_renamed") {
    return [
      { type: "session_header", session: frame.session || null },
      { type: "runtime_line", text: "session renamed", kind: "system" },
      { type: "refresh_sessions" },
    ];
  }

  if (frame.type === "session_cleared") {
    return [
      { type: "session_header", session: frame.session || null },
      { type: "messages_loaded", messages: [] },
      { type: "runtime_line", text: "session cleared", kind: "system" },
      { type: "refresh_sessions" },
    ];
  }

  if (frame.type === "session_switched") {
    return [
      { type: "session_selected", session: frame.session || {} },
      { type: "runtime_line", text: `switched to ${frame.session?.key || ""}`, kind: "system" },
    ];
  }

  if (frame.type === "session_deleted") {
    return [
      { type: "session_deleted", sessionKey: frame.session_key || "" },
      { type: "runtime_line", text: `deleted ${frame.session_key}`, kind: "system" },
      { type: "refresh_sessions" },
    ];
  }

  if (frame.type === "pong") {
    return [{ type: "runtime_line", text: "pong", kind: "system" }];
  }

  if (frame.type === "error") {
    return [
      { type: "status", status: "error", tone: "idle" },
      { type: "runtime_line", text: `error: ${frame.message}`, kind: "error" },
    ];
  }

  return [{ type: "runtime_line", text: `error: unsupported frame ${frame.type || "(empty)"}`, kind: "error" }];
}

export function reduceGatewayEvent(event) {
  if (!event || typeof event !== "object") {
    return [];
  }

  if (event.kind === "runtime") {
    return reduceRuntimeEvent(event.event_type, event.payload || {});
  }

  if (event.kind === "outbound") {
    return reduceOutboundEvent(event.payload || {});
  }

  return [];
}

export function reduceRuntimeEvent(type, payload) {
  if (type === "turn_run_status_changed") {
    const status = payload.status || "running";
    const detailMap = {
      building_context: "Assembling prompt, memory, tools, and session context.",
      running: "Turn accepted and now entering the main loop.",
      calling_model: "The model is thinking about the next reply or tool action.",
      executing_tools: "The agent is currently executing one or more tools.",
      completed: "The turn finished successfully.",
      error: payload.detail || "The turn failed before completion.",
    };
    return [
      { type: "status", status, tone: status === "completed" ? "ok" : "active" },
      {
        type: "stage",
        stage: status,
        detail: detailMap[status] || "The runtime is progressing through the current turn.",
      },
      { type: "runtime_line", text: `status: ${status}`, kind: "status" },
    ];
  }

  if (type === "tool_call_started") {
    return [
      { type: "tool_state", toolName: payload.tool_name || "tool", toolState: "running" },
      { type: "runtime_line", text: `tool start: ${payload.tool_name}`, kind: "tool" },
    ];
  }

  if (type === "tool_call_completed") {
    const toolState = payload.is_error ? "error" : "done";
    return [
      { type: "tool_state", toolName: payload.tool_name || "tool", toolState },
      {
        type: "runtime_line",
        text: `${payload.is_error ? "tool error" : "tool done"}: ${payload.tool_name}`,
        kind: payload.is_error ? "error" : "tool",
      },
    ];
  }

  if (type === "turn_completed") {
    return [
      { type: "status", status: "completed", tone: "ok" },
      {
        type: "stage",
        stage: "completed",
        detail: `Turn finished with stop reason: ${payload.stop_reason || "completed"}.`,
      },
      { type: "runtime_line", text: `turn complete: ${payload.stop_reason || "completed"}`, kind: "status" },
      { type: "refresh_sessions" },
    ];
  }

  return [];
}

export function reduceOutboundEvent(payload) {
  const metadata = payload.metadata || {};
  if (metadata._stream_delta) {
    return [{ type: "assistant_stream_delta", text: payload.text || "" }];
  }

  if (payload.final) {
    return [
      {
        type: "assistant_final",
        text: metadata._streamed ? "" : payload.text || "",
        streamed: Boolean(metadata._streamed),
      },
      { type: "refresh_sessions" },
    ];
  }

  return [];
}
