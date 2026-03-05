# AGENTS.md

## Safety Policy

Do not read, list, grep, edit, or execute from blocked paths unless the user explicitly asks and policy is updated.

Blocked paths (default):
- `.env`
- `.git/**`
- `.openclaw/memory/**`
- `secrets/**`
- `private/**`
- `node_modules/**`

## Forbidden Actions

- Do not dump large directories or hidden metadata by default.
- Do not expose secrets or credentials in outputs.
- Do not run destructive commands unless explicitly requested.

## Memory Policy

- Use OpenClaw memory tools when needed:
  - `memory_search` -> `memory_get` -> `memory_store`
- Keep memory reads minimal and task-relevant.
