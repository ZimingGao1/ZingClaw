import { buildAppViewModel } from "/webui/view-model.js";

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

export function createAppRenderer(els, hooks = {}) {
  function isNearBottom(element, threshold = 48) {
    return element.scrollHeight - element.clientHeight - element.scrollTop <= threshold;
  }

  function setHidden(element, hidden) {
    if (!element) {
      return;
    }
    element.hidden = hidden;
  }

  let followMessages = true;
  let followRuntimeFeed = true;

  function syncJumpButtons() {
    setHidden(els.transcriptJumpBtn, followMessages || !els.messages || els.messages.childElementCount === 0);
    setHidden(
      els.runtimeJumpBtn,
      followRuntimeFeed || !els.runtimeFeed || els.runtimeFeed.childElementCount === 0,
    );
  }

  if (els.messages) {
    els.messages.addEventListener("scroll", () => {
      followMessages = isNearBottom(els.messages);
      syncJumpButtons();
    });
  }
  if (els.runtimeFeed) {
    els.runtimeFeed.addEventListener("scroll", () => {
      followRuntimeFeed = isNearBottom(els.runtimeFeed);
      syncJumpButtons();
    });
  }
  els.transcriptJumpBtn?.addEventListener("click", () => {
    if (!els.messages) {
      return;
    }
    followMessages = true;
    els.messages.scrollTop = els.messages.scrollHeight;
    syncJumpButtons();
  });
  els.runtimeJumpBtn?.addEventListener("click", () => {
    if (!els.runtimeFeed) {
      return;
    }
    followRuntimeFeed = true;
    els.runtimeFeed.scrollTop = els.runtimeFeed.scrollHeight;
    syncJumpButtons();
  });

  function renderStatus(vm) {
    els.runtimeStatus.textContent = vm.status.text;
    els.statusDot.classList.remove("ok", "idle");
    if (vm.status.tone === "ok") {
      els.statusDot.classList.add("ok");
    } else if (vm.status.tone === "idle") {
      els.statusDot.classList.add("idle");
    }
  }

  function renderHeader(vm) {
    els.sessionTitle.textContent = vm.header.title;
    els.sessionSubtitle.textContent = vm.header.subtitle;
    els.modelLabel.textContent = vm.header.model;
  }

  function renderActions(vm) {
    if (els.reconnectBtn) {
      els.reconnectBtn.disabled = !vm.actions.canReconnect;
    }
    if (els.renameSessionBtn) {
      els.renameSessionBtn.disabled = !vm.actions.canManageSession;
    }
    if (els.clearSessionBtn) {
      els.clearSessionBtn.disabled = !vm.actions.canManageSession;
    }
    if (els.deleteSessionBtn) {
      els.deleteSessionBtn.disabled = !vm.actions.canManageSession;
    }
    if (els.sendBtn) {
      els.sendBtn.disabled = !vm.actions.canSend;
    }
    if (els.composerInput) {
      els.composerInput.disabled = !vm.actions.canSend;
    }
    if (els.composerHint) {
      els.composerHint.textContent = vm.composer.hint;
    }
  }

  function renderStage(vm) {
    els.stageBadge.textContent = vm.stage.label;
    els.stageBadge.className = `stage-badge ${vm.stage.name}`;
    els.stageDetail.textContent = vm.stage.detail;
  }

  function renderToolStrip(vm) {
    els.toolCount.textContent = vm.tools.count;
    if (vm.tools.isEmpty) {
      els.toolStrip.innerHTML = `<div class="empty-state compact">No active tools right now.</div>`;
      return;
    }
    els.toolStrip.innerHTML = vm.tools.items
      .map(
        (tool) => `
          <div class="tool-chip ${escapeHtml(tool.state)}">
            <span>${escapeHtml(tool.name)}</span>
            <span class="tool-chip-state">${escapeHtml(tool.state)}</span>
          </div>
        `,
      )
      .join("");
  }

  function renderRuntimeFeed(vm) {
    const previousScrollTop = els.runtimeFeed.scrollTop;
    const shouldStickToBottom = followRuntimeFeed || isNearBottom(els.runtimeFeed);

    if (vm.runtimeFeed.isEmpty) {
      els.runtimeFeed.innerHTML = `<div class="empty-state">Runtime progress will appear here once a turn starts.</div>`;
      els.runtimeFeed.scrollTop = 0;
      followRuntimeFeed = true;
      syncJumpButtons();
      return;
    }
    els.runtimeFeed.innerHTML = vm.runtimeFeed.lines
      .map(
        (line) => `<div class="runtime-item ${escapeHtml(line.kind || "system")}">${escapeHtml(line.text)}</div>`,
      )
      .join("");
    if (shouldStickToBottom) {
      els.runtimeFeed.scrollTop = els.runtimeFeed.scrollHeight;
      followRuntimeFeed = true;
      syncJumpButtons();
      return;
    }

    const maxScrollTop = Math.max(0, els.runtimeFeed.scrollHeight - els.runtimeFeed.clientHeight);
    els.runtimeFeed.scrollTop = Math.min(previousScrollTop, maxScrollTop);
    syncJumpButtons();
  }

  function renderSessions(vm) {
    if (els.sessionSearchInput) {
      els.sessionSearchInput.value = vm.sessions.searchQuery;
    }
    if (els.sessionSortSelect) {
      els.sessionSortSelect.value = vm.sessions.sortKey;
    }
    els.sessionCount.textContent = vm.sessions.count;
    if (vm.sessions.isEmpty) {
      els.sessionsList.innerHTML = `<div class="empty-state">No sessions yet. Start a new chat to create one.</div>`;
      return;
    }
    els.sessionsList.innerHTML = vm.sessions.items
      .map(
        (session) => `
          <button class="session-card ${session.isActive ? "active" : ""}" data-session-key="${escapeHtml(session.key)}" type="button">
            <div class="session-title">${escapeHtml(session.title)}</div>
            <div class="session-preview">${escapeHtml(session.preview)}</div>
            <div class="session-meta">${escapeHtml(session.lastActiveLabel)}</div>
          </button>
        `,
      )
      .join("");

    for (const button of els.sessionsList.querySelectorAll(".session-card")) {
      button.addEventListener("click", () => hooks.onOpenSession?.(button.dataset.sessionKey));
    }
  }

  function renderMessages(vm) {
    const previousScrollTop = els.messages.scrollTop;
    const shouldStickToBottom = followMessages || isNearBottom(els.messages);

    if (vm.messages.isEmpty) {
      els.messages.innerHTML = `<div class="empty-state">This session is empty. Send a message to begin.</div>`;
      els.messages.scrollTop = 0;
      followMessages = true;
      syncJumpButtons();
      return;
    }
    els.messages.innerHTML = "";
    for (const item of vm.messages.items) {
      const node = els.messageTemplate.content.firstElementChild.cloneNode(true);
      node.classList.add(item.role);
      node.querySelector(".message-role").textContent = item.role;
      node.querySelector(".message-time").textContent = item.timeLabel || "";
      node.querySelector(".message-body").textContent = item.content || "";
      els.messages.appendChild(node);
    }
    if (shouldStickToBottom) {
      els.messages.scrollTop = els.messages.scrollHeight;
      followMessages = true;
      syncJumpButtons();
      return;
    }

    const maxScrollTop = Math.max(0, els.messages.scrollHeight - els.messages.clientHeight);
    els.messages.scrollTop = Math.min(previousScrollTop, maxScrollTop);
    syncJumpButtons();
  }

  function renderMcp(vm) {
    if (vm.mcp.isEmpty) {
      els.mcpSummary.textContent = vm.mcp.summary;
      els.mcpList.innerHTML = `<div class="empty-state">No MCP servers configured yet.</div>`;
      return;
    }
    els.mcpSummary.textContent = vm.mcp.summary;
    els.mcpList.innerHTML = vm.mcp.items
      .map(
        (server) => `
          <div class="mcp-card">
            <div class="mcp-titleline">
              <div class="mcp-name">${escapeHtml(server.name)}</div>
              <div class="mcp-state ${server.stateClass}">${escapeHtml(server.stateLabel)}</div>
            </div>
            <div class="mcp-details">
              <div>transport: ${escapeHtml(server.transport)}</div>
              <div>tools/resources/prompts: ${escapeHtml(`${server.toolCount}/${server.resourceCount}/${server.promptCount}`)}</div>
              <div>prefix: ${escapeHtml(server.toolPrefix)}</div>
              ${server.target ? `<div>target: ${escapeHtml(server.target)}</div>` : ""}
            </div>
            ${server.toolNames.length ? `<div class="mcp-details">tools: ${escapeHtml(server.toolNames.join(", "))}</div>` : ""}
            ${server.resourceNames.length ? `<div class="mcp-details">resources: ${escapeHtml(server.resourceNames.join(", "))}</div>` : ""}
            ${server.promptNames.length ? `<div class="mcp-details">prompts: ${escapeHtml(server.promptNames.join(", "))}</div>` : ""}
            ${server.error ? `<div class="mcp-details">${escapeHtml(server.error)}</div>` : ""}
          </div>
        `,
      )
      .join("");
  }

  return {
    render(state) {
      const vm = buildAppViewModel(state);
      if (els.runtimeFilterSelect) {
        els.runtimeFilterSelect.value = vm.runtimeFeed.filter;
      }
      renderStatus(vm);
      renderHeader(vm);
      renderActions(vm);
      renderStage(vm);
      renderToolStrip(vm);
      renderRuntimeFeed(vm);
      renderSessions(vm);
      renderMessages(vm);
      renderMcp(vm);
    },
  };
}
