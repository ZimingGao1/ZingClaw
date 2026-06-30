---
name: weather
description: Get current weather and forecasts using free HTTP services.
metadata: {"nanobot":{"requires":{"bins":["curl"]}}}
---

# Weather

Use the `exec` tool with `curl` for quick weather lookups.

## wttr.in (primary)

Quick one-liner:

```bash
curl -s "wttr.in/London?format=3"
```

Compact format:

```bash
curl -s "wttr.in/London?format=%l:+%c+%t+%h+%w"
```

Full forecast:

```bash
curl -s "wttr.in/London?T"
```

Tips:

- URL-encode spaces, for example `wttr.in/New+York`
- Airport codes also work, for example `wttr.in/JFK`
- `?m` for metric and `?u` for US units
- `?1` for today only and `?0` for current conditions only

## Open-Meteo (fallback)

For programmatic JSON output:

```bash
curl -s "https://api.open-meteo.com/v1/forecast?latitude=51.5&longitude=-0.12&current_weather=true"
```

Use this when the user needs structured weather data rather than a human-readable sentence.
