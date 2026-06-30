export function appendUserMessage(state, content) {
  state.messages.push({
    role: "user",
    content,
    timestamp: new Date().toISOString(),
  });
}

export function resetTranscript(state) {
  state.messages = [];
  state.pendingAssistantId = null;
}

export function replaceMessages(state, messages) {
  state.messages = Array.isArray(messages) ? [...messages] : [];
  state.pendingAssistantId = null;
}

export function ensurePendingAssistant(state) {
  if (!state.pendingAssistantId) {
    const id = crypto.randomUUID();
    state.pendingAssistantId = id;
    state.messages.push({
      id,
      role: "assistant",
      content: "",
      timestamp: new Date().toISOString(),
    });
  }
  return state.pendingAssistantId;
}

export function appendAssistantDelta(state, text) {
  const pendingId = ensurePendingAssistant(state);
  const pending = state.messages.find((item) => item.id === pendingId);
  if (pending) {
    pending.content += text || "";
  }
}

export function finalizeAssistant(state, text = "", streamed = false) {
  if (!streamed && text) {
    state.messages.push({
      role: "assistant",
      content: text,
      timestamp: new Date().toISOString(),
    });
  }
  state.pendingAssistantId = null;
}
