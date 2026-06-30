---
name: long-goal
description: Sustained objectives via long_task / complete_goal, with idempotent goal wording and session-visible runtime context.
---

# Long-running objectives (`long_task` / `complete_goal`)

Use these tools when the user wants multi-turn sustained work on one clear objective. Do not use them for trivial one-shot questions.

## Start fast

`long_task` is a lightweight marker. It records one sustained objective on the current session so future turns keep seeing it in Runtime Context.

Call `long_task` as soon as the user's intent is clear. Write a good `goal` immediately:

1. Make it self-contained.
2. Make it bounded.
3. Make it explicit about done-ness.
4. Make it safe to re-read after retries or history compaction.

Do not delay the first `long_task` call just to over-plan.

## Tool lifecycle

- `long_task`: Start one active sustained goal for the current session.
- `complete_goal`: End bookkeeping for the active goal when it is done, cancelled, redirected, or replaced.

If a goal is already active and the user wants a different one, close the old goal honestly with `complete_goal`, then start the new one with `long_task`.

## Goal wording

Prefer goals that describe the target state rather than fragile step order.

Good properties:

1. Idempotent: re-reading the goal should not imply duplicate destructive work.
2. Specific: mention paths, repos, files, constraints, or acceptance criteria when they matter.
3. Bounded: say what is in scope and what is out of scope.
4. Verifiable: include how you will know the work is complete.

## Execution after the marker is set

Once `long_task` is set, continue with ordinary tools:

- use `find_files` / `grep` to discover relevant code,
- use `read_file` / `edit_file` / `write_file` to inspect and change files,
- use `exec` for verification,
- use `web_fetch` when external docs or references are needed.

Keep progress grounded in the stored goal. If the task becomes ambiguous, either ask the user or end the current goal and register a narrower replacement.
