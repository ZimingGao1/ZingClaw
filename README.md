# ZingClaw

ZingClaw is a `nanobot`-inspired local agent runtime under active construction.

This repository is intentionally split into two layers:

- `reference/`: copied source and tests kept for study and selective migration
- `zingclaw/`: the real product code we will implement and evolve

## Current Phase

The repository has been restructured and is ready for `P0` implementation:

- isolated reference code under `reference/`
- created the `zingclaw/` package skeleton
- added minimal settings + CLI entrypoints
- added a small top-level test suite for the new package

## Planned P0 Scope

- CLI entrypoint
- OpenAI-compatible provider
- tool registry
- `list_dir`
- `read_file`
- `write_file`
- `exec`
- session persistence
- loop / runner tool-call flow

## Repository Layout

```text
ZingClaw/
  reference/       # copied nanobot code kept as migration material
  zingclaw/        # your real implementation
  tests/           # new tests for ZingClaw itself
  workspace/       # local workspace for file tools
  sessions/        # session history
  memory/          # long-term memory artifacts
  logs/            # run traces and logs
```

## Next Step

The next implementation target is the first runnable `P0` slice:

1. `config/settings.py`
2. `providers/base.py`
3. `tools/base.py` + `tools/registry.py`
4. `session/manager.py`
5. `agent/runner.py`
6. `agent/loop.py`

See [vibe-clone-implementation-plan.md](D:\Project\ZingClaw\vibe-clone-implementation-plan.md) for the full build plan.
