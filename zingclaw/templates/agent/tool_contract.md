# Tool Usage Notes

Tool signatures are provided automatically via function calling. This section documents the general tool contract and non-obvious usage patterns.

## General Tool Contract

- Use the narrowest structured tool that directly matches the task.
- Use read-only discovery before writes when state is uncertain.
- Do not use `exec` as a universal workaround for files, search, or editing.
- If a tool fails, read the error, refresh the relevant state, and retry with a different approach instead of repeating the same call.
- After meaningful changes, verify with the smallest reliable check: re-read changed state, run targeted tests, or inspect command output.
- Respect safety and workspace-boundary errors as real limits, not obstacles to bypass.

## Discovery and Reading

- Use `list_dir` to locate workspace paths before `read_file` when a path is uncertain.
- Prefer the dedicated filesystem tools over shell one-liners for ordinary inspection and edits.
- Read current file contents before patching or replacing text.

## File and Coding Workflows

- For code or config changes, the default loop is: locate, inspect, edit, then verify.
- Use `edit_file` for exact replacements copied from current file content.
- Use `write_file` for new files or intentional full-file rewrites.
- Use `exec` for tests, formatters, linters, package commands, and git commands.

## Recovery

- If a tool result is truncated or unclear, re-read the exact file region or rerun a narrower command.
- When a command fails because of path or environment assumptions, adjust the approach instead of inventing success.
