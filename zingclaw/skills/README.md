# ZingClaw Skills

This directory contains built-in skills that extend ZingClaw's capabilities.

## Skill Format

Each skill is a directory containing a `SKILL.md` file with:
- YAML frontmatter (`name`, `description`, `metadata`, `always`, etc.)
- Markdown instructions for the agent

`ZingClaw` follows the same general skill shape used by `nanobot` / `OpenClaw`,
while keeping the content truthful to ZingClaw's current toolset.

## Skill Source Order

Skills are merged from three locations:

1. `workspace/skills`
2. configured `skills_dir`
3. built-in `zingclaw/skills`

Earlier sources override later ones by skill name.

## Supported Metadata

The current loader understands:

- `description`: one-line summary shown in the skills summary section
- `always`: when `true`, inject the full skill into the system prompt
- `metadata.requires.bins`: required CLI commands
- `metadata.requires.env`: required environment variables

Nested compatibility payloads such as `metadata.nanobot` and `metadata.openclaw`
are also supported.

## Disabling Skills

You can disable skills in config with:

```json
{
  "agent": {
    "disabledSkills": ["memory"]
  }
}
```

Disabled skills are excluded from discovery, summaries, and prompt injection.

You can also explicitly whitelist skills:

```json
{
  "agent": {
    "enabledSkills": ["memory", "cron"]
  }
}
```

When `enabledSkills` is non-empty, only those skills are considered active.

## Available Skills

| Skill | Description |
|-------|-------------|
| `cron` | Schedule reminders and recurring tasks with the built-in `cron` tool |
| `github` | Interact with GitHub using the `gh` CLI |
| `long-goal` | Sustained objectives using `long_task` and `complete_goal` |
| `memory` | Session memory, archived history, and long-term notes guidance |
| `weather` | Get current weather and forecasts using free HTTP services |
