---
name: memory
description: Two-layer memory system with session history, archived history, and long-term notes.
always: true
---

# Memory

## Structure

- `SOUL.md` and `USER.md` are bootstrap guidance files.
- `memory/MEMORY.md` stores durable long-term facts and preferences.
- `memory/HISTORY.md` stores readable archived conversation summaries.
- `memory/history.jsonl` stores the structured archive source used by the runtime.
- `sessions/*.jsonl` stores active session history and session metadata.

## Reading strategy

- Read `memory/MEMORY.md` when long-running project context or user preferences matter.
- Read `sessions/*.jsonl` when the active conversation state matters.
- Read `memory/HISTORY.md` when older archived context matters.
- Use `memory/history.jsonl` only when you need the structured machine-readable archive.
- Prefer `read_file` for smaller files and short targeted inspection.
- If archived history is too large to page through comfortably, use `grep` for targeted searches.

## Writing strategy

- Use the built-in `save_memory` tool when a fact should become durable long-term memory.
- Only save stable preferences, project constraints, or important decisions.
- Do not write speculative, temporary, or already-obsolete notes into `MEMORY.md`.

## Important

- Do not invent remembered facts that are not present in memory or session history.
- Prefer archived summaries and session state over repeating old work.
- Treat memory files as durable context, not as permission to override current user intent.
