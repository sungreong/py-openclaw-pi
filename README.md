# OpenClaw Pi (LangChain)

Minimal coding agent runtime inspired by OpenClaw.

## Quick Start

1. Copy `.env.example` to `.env`
2. Set `OPENAI_API_KEY`
3. Optional: adjust `PI_READ_STRATEGY` (`smart` or `legacy`)
4. Run:
   - CLI: `python openclaw_pi_langchain.py "your prompt"`
   - Chat: `python chat.py`

## Tool Registry (Builtin + Custom + MCP)

Pi merges tools in this order:
1. Builtin tools (`read`, `write`, `edit`, `ls`, `find`, `grep`, `exec`, memory tools, todo tools)
2. Custom Python tools (module loader)
3. MCP tools (from `mcp_servers.json`)

Final tool access is still controlled by `--allow-tool` / `--deny-tool`.

### Custom tool modules

Load custom tools with repeatable CLI args:

```powershell
python openclaw_pi_langchain.py "2+2 계산해줘" `
  --custom-tool-module tools.my_tools `
  --custom-tool-module .\tools\extra_tools.py
```

Concrete examples in this repo:
- `sample/custom_tools_fn.py` (`@tool` + `get_tools()`)
- `sample/custom_tools_structured.py` (`StructuredTool` + `TOOLS`)
- `sample/custom_tools_class.py` (`BaseTool` with `_run` and `_arun`)

```powershell
# 1) function tool sample (@tool)
python openclaw_pi_langchain.py "calculator로 12*(3+4) 계산해줘" `
  --custom-tool-module .\sample\custom_tools_fn.py

# 2) StructuredTool sample
python openclaw_pi_langchain.py "repeat_text로 hello를 3번 반복해줘" `
  --custom-tool-module .\sample\custom_tools_structured.py

# 3) BaseTool async-capable sample
python openclaw_pi_langchain.py "delay_echo로 hi를 호출해줘" `
  --custom-tool-module .\sample\custom_tools_class.py

# 4) load multiple custom modules at once
python openclaw_pi_langchain.py "사용 가능한 custom tool을 요약해줘" `
  --custom-tool-module .\sample\custom_tools_fn.py `
  --custom-tool-module .\sample\custom_tools_structured.py `
  --custom-tool-module .\sample\custom_tools_class.py
```

Supported module contracts:
- `get_tools() -> list[Any]`
- `TOOLS = [ ... ]`

Supported tool shapes:
- `@tool` function tools
- `StructuredTool`
- `BaseTool` subclasses (`_run`, `_arun`)

Conflict policy:
- Name collision with builtin tool: startup error
- Name collision between custom tools: auto-renamed to `custom.<module>.<name>`

### MCP server tools

Default config file: `mcp_servers.json` in workspace root.

Example config (`mcp_servers.json`):

```json
{
  "servers": [
    {
      "name": "filesystem",
      "enabled": false,
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "env": {},
      "timeout_s": 20
    },
    {
      "name": "custom_python",
      "enabled": false,
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "your_mcp_server"],
      "env": {
        "API_KEY": "${YOUR_API_KEY}"
      },
      "timeout_s": 30
    }
  ]
}
```

Run with MCP config:

```powershell
python openclaw_pi_langchain.py "MCP tool 목록 확인해줘" --mcp-config .\mcp_servers.json
```

## Skill System (OpenClaw-style SKILL.md)

Skills are loaded from:
- `<workspace>/skills/*/SKILL.md`

Sample skill included:
- `skills/github-triage/SKILL.md`
- `skills/csv-basic/SKILL.md`
- `skills/csv-basic/scripts/csv_stats_tool.py`
- `skills/csv-basic/data/sample_sales.csv`

Each skill uses YAML frontmatter + markdown body.

Example fields:
- `id`, `name`, `description`
- `triggers`
- `required_tools`, `required_env`
- `tool_allow`, `tool_deny`
- `api_policy` (`tool_first` recommended)
- `output_format`

Selection modes:
- `--skills-enabled` / `--no-skills`
- `--skill <name>`: explicit skill
- `--skill-mode auto`: auto select by trigger/name/description matching
- `--skill-mode manual`: apply only when `--skill` is provided
- `--skill-mode off`: disable skills
- `--list-skills`: print discovered skills and exit

CLI examples:

```powershell
# list skills
python openclaw_pi_langchain.py --list-skills --workspace .

# scenario A: local csv summary (no API)
python openclaw_pi_langchain.py "skills/csv-basic/data/sample_sales.csv sales column mean/max" `
  --workspace . `
  --skills-enabled `
  --skill csv-basic `
  --skill-mode manual `
  --custom-tool-module .\skills\csv-basic\scripts\csv_stats_tool.py

# explicit skill
python openclaw_pi_langchain.py "open issues triage 해줘" `
  --skill github-triage `
  --skill-mode manual

# auto selection
python openclaw_pi_langchain.py "깃허브 이슈 우선순위 정리해줘" `
  --skill-mode auto
```

Chat commands (`python chat.py`):
- `/skills`
- `/skill <name>`
- `/skill auto`
- `/skill off`
- `/plan`
- `/plan on`
- `/plan off`

API policy behavior:
- `tool_first` means MCP/custom tools are preferred for API calls.
- If required tool is missing and `exec` is allowed, skill guidance permits `curl` fallback.
- Secrets must come from environment variables, never hardcoded in SKILL files.

Tool naming is normalized to:
- `mcp.<server>.<tool>`

Transport support in this version:
- `stdio` only (other transports are skipped with audit warning)

CLI flags:
- `--mcp-enabled`
- `--mcp-config <path>`
- `--no-mcp`
- `--mcp-fail-fast`
- `--mcp-timeout <seconds>`

If one MCP server fails, Pi continues with remaining servers by default.
Use `--mcp-fail-fast` to make startup fail on the first MCP connection error.

## Multi-Agent Session Examples

Use different `PI_SESSION` values to separate each agent's context and history.

### 1) Code analysis agent (read-only)

```powershell
python openclaw_pi_langchain.py "코드 구조를 요약하고 리스크 3개를 찾아줘" `
  --session analyst `
  --deny-tool write `
  --deny-tool edit `
  --deny-tool exec
```

### 2) Runner agent (execute only)

```powershell
python openclaw_pi_langchain.py "테스트 실행하고 실패 원인 요약해줘" `
  --session runner `
  --allow-tool ls `
  --allow-tool find `
  --allow-tool grep `
  --allow-tool read `
  --allow-tool exec
```

### 3) Fixer agent (edit + validate)

```powershell
python openclaw_pi_langchain.py "실패 테스트를 최소 수정으로 고치고 검증해줘" `
  --session fixer
```

### 4) Interactive chat per role

```powershell
$env:PI_SESSION="analyst"; python chat.py
$env:PI_SESSION="runner"; python chat.py
$env:PI_SESSION="fixer"; python chat.py
```

Recommended workflow:
1. `analyst` finds issue scope
2. `runner` reproduces with commands
3. `fixer` applies patch and re-validates

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
- `PI_TOOL_REPEAT_LIMIT` (default `3`; aborts when identical tool calls repeat this many times in one run)
- `PI_EXEC_TIMEOUT`
- `PI_NO_MEMORY`
- `PI_MEMORY_MODE`
- `PI_MEMORY_DIR`
- `PI_BLOCKED_PATHS`
- `PI_READ_STRATEGY` (`smart` default, or `legacy`)
- `PI_EXEC_PATH_CORRECTION` (default `false`; enables limited safe exec path correction)
- `PI_CUSTOM_TOOL_MODULES` (comma-separated module refs)
- `PI_MCP_ENABLED` (`true`/`false`)
- `PI_MCP_CONFIG` (default `mcp_servers.json`)
- `PI_MCP_FAIL_FAST` (`true`/`false`)
- `PI_MCP_TIMEOUT` (seconds, default `20`)
- `PI_SKILLS_ENABLED` (`true`/`false`)
- `PI_SKILLS_DIR` (default `skills`)
- `PI_SKILL_MODE` (`auto|manual|off`)
- `PI_SKILL` (optional explicit skill id/name)
- `PI_PLAN_MODE` (`on|off`, default `off`)

## Todo Tools (Session Task Tracking)

Pi includes built-in `todo_read` and `todo_write` tools for tracking tasks within a session.

- `todo_write`: Replace the session todo list with a JSON array of items
- `todo_read`: Read the current todo list with status icons and priorities

### Usage in chat

Ask Pi naturally:

```
Show me the todo list
Create a todo list: 1) Fix bug (high), 2) Write tests (medium)
Mark the first item as completed
```

For multi-step tasks, Pi automatically creates and updates a todo list when both tools are active.

### todo_write format

Input is a JSON array:

```json
[
  {"content": "Fix the login bug", "status": "pending", "priority": "high"},
  {"content": "Write unit tests", "status": "in_progress", "priority": "medium"},
  {"content": "Update docs", "priority": "low"}
]
```

Fields:
- `content` (required): task description
- `status`: `pending` | `in_progress` | `completed` | `cancelled` (default: `pending`)
- `priority`: `high` | `medium` | `low` (default: `medium`)

IDs are assigned automatically (1-based).

### todo_read output format

```
[ ] [high] #1 Fix the login bug
[~] [medium] #2 Write unit tests
[ ] [low] #3 Update docs
```

Status icons: `[ ]` pending · `[~]` in_progress · `[x]` completed · `[-]` cancelled

> **Note:** Todo state is session-scoped (in-memory). It resets when the agent restarts — same behavior as Claude Code's built-in TodoRead/TodoWrite.

## Plan Mode (Claude-style)

- Enable with chat command `/plan on`, CLI `--plan-mode on`, or env `PI_PLAN_MODE=on`.
- In plan mode, Pi enforces read-only planning behavior:
  - blocked tools: `write`, `edit`, `exec`, `memory_store`
  - allowed tools (subject to existing policy): `read`, `ls`, `find`, `grep`, `memory_search`, `memory_get`
- Skill precheck failures are relaxed in plan mode so Pi can still return a plan response.
- Legacy auto-memory write is disabled while plan mode is on.
- Disable with `/plan off` or `--plan-mode off`.

## Exec Failure Guard (v1)

- `exec` output keeps the existing text format and now appends:
  - `result=ok|error`
  - `error_type`
  - `error_signature`
  - `retryable=true|false`
- Repeated identical `exec` failures are blocked per session to prevent loops.
- Recent failures are injected as a short failure digest to encourage strategy changes.

## Tool Repeat Guard (v1)

- Repeated identical tool calls are tracked per run by `tool_name + normalized args`.
- If the same call repeats `PI_TOOL_REPEAT_LIMIT` times (default `3`), the run is aborted to stop loops.
- After abort, Pi performs one recovery model pass without tools and:
  - returns the best direct answer from collected context, or
  - asks one precise follow-up question when context is insufficient.

## Read Token Efficiency (v1)

- No new tools added; `read` now supports `full` flag (`read(path, full=true)` for full content).
- With `PI_READ_STRATEGY=smart` (default), large files return metadata + head/tail preview:
  - `line_count`, `char_count`, `truncated=true`, and a `grep` hint.
- With `PI_READ_STRATEGY=legacy`, `read` keeps full-read behavior.
- Per-turn read output budget guard is enabled (default 20,000 chars) to reduce repeated large reads.
