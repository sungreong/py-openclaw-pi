# OpenClaw Pi (LangChain)

Minimal coding agent runtime inspired by OpenClaw.

## Quick Start

1. Copy `.env.example` to `.env`
2. Set `OPENAI_API_KEY`
3. Optional: adjust `PI_READ_STRATEGY` (`smart` or `legacy`)
4. Run:
   - CLI: `python openclaw_pi_langchain.py "your prompt"`
   - Chat: `python chat.py`

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

- `OPENAI_API_KEY` (required)
- `PI_MODEL`
- `PI_WORKSPACE`
- `PI_SESSION`
- `PI_MAX_MODEL_CALLS`
- `PI_EXEC_TIMEOUT`
- `PI_NO_MEMORY`
- `PI_MEMORY_MODE`
- `PI_MEMORY_DIR`
- `PI_BLOCKED_PATHS`
- `PI_READ_STRATEGY` (`smart` default, or `legacy`)
- `PI_EXEC_PATH_CORRECTION` (default `false`; enables limited safe exec path correction)

## Exec Failure Guard (v1)

- `exec` output keeps the existing text format and now appends:
  - `result=ok|error`
  - `error_type`
  - `error_signature`
  - `retryable=true|false`
- Repeated identical `exec` failures are blocked per session to prevent loops.
- Recent failures are injected as a short failure digest to encourage strategy changes.

## Read Token Efficiency (v1)

- No new tools added; `read` now supports `full` flag (`read(path, full=true)` for full content).
- With `PI_READ_STRATEGY=smart` (default), large files return metadata + head/tail preview:
  - `line_count`, `char_count`, `truncated=true`, and a `grep` hint.
- With `PI_READ_STRATEGY=legacy`, `read` keeps full-read behavior.
- Per-turn read output budget guard is enabled (default 20,000 chars) to reduce repeated large reads.
