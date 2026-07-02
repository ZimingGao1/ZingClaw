---
name: cron
description: Schedule reminders and recurring tasks with the built-in cron tool.
---

# Cron

Use the `cron` tool to schedule reminders or recurring tasks.

## Three Modes

1. Reminder: send a message back to the user later.
2. Task: store a task-like message that the agent should act on when triggered.
3. One-time: run once at a specific time and then disappear.

## Examples

Fixed reminder:

```text
cron(action="add", message="Time to take a break!", every_seconds=1200)
```

Dynamic recurring task:

```text
cron(action="add", message="Check the repository status and summarize changes", every_seconds=600)
```

One-time reminder:

```text
cron(action="add", message="Remind me about the meeting", at="<ISO datetime>")
```

Timezone-aware cron schedule:

```text
cron(action="add", message="Morning standup", cron_expr="0 9 * * 1-5", tz="Asia/Shanghai")
```

List or remove:

```text
cron(action="list")
cron(action="remove", job_id="abc123")
```

## Time Expressions

- "every 20 minutes" -> `every_seconds=1200`
- "every hour" -> `every_seconds=3600`
- "every day at 8am" -> `cron_expr="0 8 * * *"`
- "weekdays at 5pm" -> `cron_expr="0 17 * * 1-5"`
- "at a specific time" -> compute and pass `at=<ISO datetime>`

## Guidance

- Prefer the `cron` tool over shell scheduling.
- Use `tz` when the user mentions a timezone explicitly.
- Confirm the intended cadence when the schedule is ambiguous.
