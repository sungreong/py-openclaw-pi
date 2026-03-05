# OpenClaw Pi (LangChain)

Minimal coding agent runtime inspired by OpenClaw.

## Memory Modes

- `PI_MEMORY_MODE=openclaw` (default)
  - Uses tool-driven markdown memory: `memory_search`, `memory_get`, `memory_store`
  - Storage: `.openclaw/memory/MEMORY.md` and `.openclaw/memory/YYYY-MM-DD.md`
- `PI_MEMORY_MODE=legacy`
  - Uses automatic memory extract/recall compatibility flow

## Blocked Path Policy (claudeignore alternative)

This project uses `AGENTS.md` + runtime blocked-path enforcement.

Default blocked paths:
- `.env`
- `.git/**`
- `.openclaw/memory/**`
- `secrets/**`
- `private/**`
- `node_modules/**`

Configuration:
- Env: `PI_BLOCKED_PATHS` (comma-separated)
- CLI: `--blocked-path` (repeatable)

Generic file tools (`read/write/edit/ls/find/grep/exec`) do not access blocked paths.
Memory tools remain available for managed memory access.

## Key Environment Variables

- `PI_NO_MEMORY`
- `PI_MEMORY_MODE`
- `PI_MEMORY_DIR`
- `PI_BLOCKED_PATHS`
