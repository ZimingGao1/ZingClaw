---
name: memory
description: Two-layer memory system with session history, archived history, and long-term notes.
always: true
---

# Memory

## Structure

- `SOUL.md` and `USER.md` are bootstrap guidance files.
- `memory/MEMORY.md` stores durable long-term facts and preferences.
- `memory/history.jsonl` stores archived conversation summaries.
- `sessions/*.jsonl` stores active session history and session metadata.

## Reading strategy

- Read `memory/MEMORY.md` when long-running project context or user preferences matter.
- Read `sessions/*.jsonl` when the active conversation state matters.
- Read `memory/history.jsonl` when older archived context matters.
- Prefer `read_file` for smaller files and short targeted inspection.
- If archived history is too large to page through comfortably, use `grep` for targeted searches.

## Important

- Do not invent remembered facts that are not present in memory or session history.
- Prefer archived summaries and session state over repeating old work.
- Treat memory files as durable context, not as permission to override current user intent.
