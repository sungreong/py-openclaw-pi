# OpenClaw Pi (LangChain)

Minimal coding agent runtime inspired by OpenClaw.

Start with the [Korean quick start](docs/QUICKSTART.kr.md), then use the [configuration guide](docs/CONFIGURATION.kr.md) for project instructions, permissions, sessions, skills, and MCP. The [Korean getting-started guide](docs/GETTING_STARTED.kr.md) covers local Python, Docker, Local Bedrock, full options, and troubleshooting.

## Quick Start

1. Copy `.env.example` to `.env`
2. Set `OPENAI_API_KEY` or all three Local Bedrock variables
3. Optional: adjust `PI_READ_STRATEGY` (`smart` or `legacy`)
4. Run:
   - Review: `python openclaw_pi_langchain.py --mode review "your prompt"`
   - Scoped edit: `python openclaw_pi_langchain.py --mode edit --edit-path piagent/session.py "your prompt"`
   - Full tools (prefer an isolated environment): `python openclaw_pi_langchain.py --mode full "your prompt"`
   - Chat check: `python chat.py --check --workspace . --no-mcp`
   - Chat: `python chat.py --workspace . --session chat-main --mode review --no-mcp`

See the [Korean agent-level and chat guide](docs/AGENT_LEVEL_AND_CHAT.kr.md) for the full chat CLI and current maturity assessment. The [20-task GPT-OSS-120B evaluation](docs/AGENT_CAPABILITY_20_RESULT.kr.md) records actual prompts, tool use, artifacts, and limitations.

## PiAgent as a persistent subagent

Use the JSONL bridge when another agent needs one persistent PiAgent conversation:

```powershell
python -u scripts/piagent_subagent_chat.py --jsonl --session codex-job-1 --user-id codex --mode review
```

The simplified permission interface has three modes: `review` (default, read/plan only), `edit` (one exact replacement in explicitly named existing files), and `full` (all configured tools). Start scoped editing with `--mode edit --edit-path piagent/session.py`, or send `{"prompt":"...","mode":"edit","paths":["piagent/session.py"]}` for one turn. Scoped edit mode hides `write`, `multiedit`, shell, and package installation; it also rejects undeclared paths and `replace_all` at runtime.

Advanced per-turn fields such as `allowlist`, `denylist`, `skill_name`, `skill_mode`, and `plan_mode` remain available for compatibility, but cannot broaden a simplified mode. Use `{"command":"state"}` or `{"command":"exit"}` for control. Run `python scripts/run_agent_multiturn_10.py --list` to inspect the 10-scenario, 37-turn evaluation suite.

## Pinned Development Environment

The Docker development environment installs the verified Python package set from `requirements-piagent.lock.txt`.
The image build uses the lock file for package reproducibility, while `docker-compose.yml` injects `.env` only at runtime.

```powershell
docker compose build --no-cache
docker compose run --rm pi_agent python -m pytest tests/test_piagent_core.py tests/test_chat_ui.py tests/test_piagent_session.py
```

Use the test override for an isolated run without the host `.env` or development bind mount. It runs the full regression suite against the source copied into the image.

```powershell
docker compose --env-file .env.example `
  -f docker-compose.yml -f docker-compose.test.yml build pi_agent

docker compose --env-file .env.example `
  -f docker-compose.yml -f docker-compose.test.yml run --rm pi_agent
```

`docker-compose.test.yml` supplies a fake key for tests that never call a real model. Latest stock data, chart rendering, and Word/DOCX output require additional data tools and document-generation packages in the image.

To align a local Python environment with the same package versions:

```powershell
python -m pip install -r requirements-piagent.lock.txt
```

When intentionally upgrading packages, update both `requirements-piagent.txt` and the lock file, then run the full test suite.

## Workspace extensions (`.piagent`)

When trusted workspace extensions are enabled, PiAgent discovers this structure automatically:

```text
.piagent/
├─ skills/
│  └─ stock-report/
│     └─ SKILL.md
├─ tools/
│  └─ stock-price/
│     └─ tool.py
└─ packages/               # created by python_package_install
```

Enable discovery:

```powershell
$env:PI_WORKSPACE_EXTENSIONS_ENABLED="true"

# The minimal runner also accepts an explicit opt-in flag.
python simple_piagent.py --check --workspace-extensions
```

Minimal `SKILL.md`:

```markdown
---
name: stock-report
description: Use for recent stock analysis and visual report requests.
---

Use verified market data and record its observation date and source.
```

Minimal `tool.py`:

```python
from langchain.tools import tool

@tool
def stock_symbol(value: str) -> str:
    """Normalize a stock ticker symbol."""
    return value.strip().upper()

TOOLS = [stock_symbol]
```

`tool.py` must expose `TOOLS`, a list of LangChain tools, or `get_tools()` returning that list. Tool folder names accept lowercase letters, digits, and hyphens only. Builtin tool name conflicts fail startup. Enabling this feature imports Python from the workspace, so use it only with repositories you trust.

Import optional dependencies inside the tool function. If one is missing, return `missing_dependency=<PyPI name>` so the model can call `python_package_install` and retry the tool. Importing a missing third-party dependency at module scope prevents the extension from loading during agent startup.

### Connected Markdown Search MCP

The included `.piagent/tools/markdown-search/tool.py` is a bounded read-only adapter for an already-running local Streamable HTTP MCP server. It exposes `markdown_mcp_search` and `markdown_mcp_read`; the matching `markdown-mcp-research` skill restricts the workflow to those evidence tools.

The host default is `http://127.0.0.1:8811/mcp`. Docker Compose maps it to `http://host.docker.internal:8811/mcp`. Override either with `PI_MARKDOWN_SEARCH_MCP_URL`; only approved local host names are accepted.

```powershell
python simple_piagent.py --workspace-extensions --mode full `
  --skill markdown-mcp-research `
  "Search the Markdown MCP for agent runtime safety and cite the document paths."
```

Workspace extensions execute trusted Python during startup. `full` is required because the current permission profiles do not automatically trust custom tool names; the selected skill then narrows the active set to the two read-only Markdown tools and `ask_user`.

### Workspace Python package installation

The installer is disabled by default and requires an allowlist:

```powershell
$env:PI_ALLOW_PACKAGE_INSTALL="true"
$env:PI_PACKAGE_INSTALL_ALLOWLIST="python-docx==1.2.0,matplotlib==3.11.1,pandas==3.0.5,yfinance==1.6.0"
```

The model can then call `python_package_install(package, import_name, version)`. Packages are installed under `.piagent/packages`. URLs, local paths, extras, `--index-url`, and arbitrary pip options are blocked. Prefer exact version pins. The tool is unavailable in Plan mode.

## Common Usage Recipes

### User-isolated runs (`--user-id`)

Use `--user-id` to isolate reports, CSVs, images, sessions, audit logs, and memory stores by user.

```powershell
python openclaw_pi_langchain.py "Analyze sample/data.csv and write a report" `
  --user-id alice
```

Outputs are stored under paths like:

```text
artifacts/users/alice/...
artifacts/users/alice/workspace/...
```

For chat mode, set environment variables once:

PowerShell:

```powershell
$env:PI_USER_ID="alice"
$env:PI_SESSION="alice-main"
python chat.py
```

Windows CMD:

```bat
set PI_USER_ID=alice
set PI_SESSION=alice-main
python chat.py
```

Linux/macOS shell:

```bash
export PI_USER_ID=alice
export PI_SESSION=alice-main
python chat.py
```

### Read-only planning

Use this when you want analysis and an implementation plan before any edits.

```powershell
python openclaw_pi_langchain.py "Plan how to fix this feature without changing files" `
  --plan-mode on `
  --permission-mode plan
```

Plan mode blocks `write`, `edit`, `multiedit`, `exec`, `exec_readonly`, `memory_store`, and `work_note_update`. It keeps `plan_note_write` available so the plan can be saved to the session work note.

### Work notes

Pi keeps a structured work note for non-trivial planning and implementation. With `--user-id alice`, the default path is `artifacts/users/alice/work-notes/<session>.md`.

```powershell
python openclaw_pi_langchain.py "Plan this refactor and save the plan note" `
  --user-id alice `
  --session alice-main `
  --plan-mode on
```

Relevant tools:

- `work_note_read`: read the current session work note
- `work_note_update`: update a section during normal implementation mode
- `work_note_search`: grep the work note for prior decisions/errors/files
- `plan_note_write`: save plan content while in plan mode

### Long output offload

Large `read`, `grep`, `exec`, and `web_fetch` results are saved to artifacts instead of being fully injected into model context.

```powershell
python openclaw_pi_langchain.py "Find the root cause in this large log" `
  --user-id alice `
  --max-tool-result-chars 12000 `
  --tool-result-artifact-dir tool-results
```

The model receives a preview plus a path such as `full_result_path=artifacts/users/alice/tool-results/...`.

### Subagents

Pi can use the internal `delegate_task` tool with `explore`, `plan`, and `verify` read-only subagents. You can request this naturally:

```powershell
python openclaw_pi_langchain.py "Use a subagent to explore the code first, then summarize the implementation plan and verification points"
```

Disable subagents when you want one agent only:

```powershell
python openclaw_pi_langchain.py "Analyze this directly without subagents" --no-subagents
```

### Safe read-only shell checks

Pi can use `exec_readonly`; it refuses commands that are not classified as read-only.

```powershell
python openclaw_pi_langchain.py "Check git status and list available tests only"
```

Dangerous or state-changing commands may also be blocked by `exec` policy.

## Tool Registry (Builtin + Custom + MCP)

Pi merges tools in this order:
1. Builtin tools (`read`, `write`, `edit`, `ls`, `find`, `grep`, `exec`, memory tools, todo tools)
2. Custom Python tools (module loader)
3. MCP tools (from a user-provided MCP config)

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

MCP is optional. The runtime looks for `mcp_servers.json` in the workspace root only when MCP is enabled. Create that local file from the tracked template; the live config is ignored by Git because it may contain commands and credentials.

```powershell
Copy-Item examples/mcp_servers.example.json mcp_servers.json
```

Example template (`examples/mcp_servers.example.json`):

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
- `skills/data-report-writer/SKILL.md`

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
- `--hooks-config <path>`
- `--user-id <id>`

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

PowerShell:

```powershell
$env:PI_SESSION="analyst"; python chat.py
$env:PI_SESSION="runner"; python chat.py
$env:PI_SESSION="fixer"; python chat.py
```

Windows CMD:

```bat
set PI_SESSION=analyst
python chat.py
set PI_SESSION=runner
python chat.py
set PI_SESSION=fixer
python chat.py
```

Linux/macOS shell:

```bash
PI_SESSION=analyst python chat.py
PI_SESSION=runner python chat.py
PI_SESSION=fixer python chat.py
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

## Example Skill: Code Health Check

`skills/code-health-check/SKILL.md` runs its bundled Python scanner first, then creates a Markdown code-health report from observed metrics. A prompt containing `code health check` can select it automatically, or you can pass `--skill code-health-check` explicitly.

```powershell
python simple_piagent.py `
  "Run a code health check and save the report to reports/code-health.md" `
  --session code-health-demo
```

The standard-library scanner excludes `.env`, dependency directories, agent state, and private paths. Its output is a static file inventory, not evidence that tests or runtime behavior are correct.

### Company policy reference examples

- `naru-git-workflow` reads `references/git-policy.md` and applies company branch, commit, and PR rules.
- `naru-ui-design-review` reads `references/ui-policy.md` and audits HTML/CSS against company design tokens and accessibility rules.
- `naru-python-coding-guide` reads `references/python-coding-guide.md` for company-specific Python questions, reviews, and implementations.

NaruWorks is a fictional policy set for evaluation, not real company information. Detailed policy is kept under `references/`, so only the selected skill loads the relevant company context. The live coding-guide results and limitations are documented in `docs/NARU_PYTHON_CODING_GUIDE_EVAL.kr.md`.

## Session Memory Fragments

After each run, PiAgent splits the user prompt and sanitized final answer into fragments of at most 900 characters and appends them to `<session>.fragments.jsonl` under the session directory. This append-only archive survives normal history compaction. Tool results and internal reasoning are not stored.

- `session_fragment_search(query, session_id, limit, role)` returns matching fragment IDs and short snippets.
- `session_fragment_get(ids, session_id)` retrieves up to 20 full fragments selected by ID.

The agent is instructed to search first and retrieve only the needed fragments. With `--user-id`, fragments use the same per-user isolation as session history. Use this archive for exact details from the same conversation; use `memory_search`, `memory_get`, and `memory_store` for durable preferences or facts across sessions.

Example prompt:

```text
Find the output format we discussed for project ORCHID. Use session_fragment_search, then verify the relevant fragment with session_fragment_get.
```

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

- `OPENAI_API_KEY` (required unless the local Bedrock variables below are set)
- `LOCAL_BEDROCK_BASE_URL` (optional Bedrock Runtime endpoint; `/openai/v1` is appended automatically)
- `LOCAL_BEDROCK_MODEL_ID` (required with `LOCAL_BEDROCK_BASE_URL`)
- `LOCAL_BEDROCK_API_KEY` (required with `LOCAL_BEDROCK_BASE_URL`; never store it in source control)
- `PI_MODEL`
- `PI_WORKSPACE`
- `PI_SESSION`
- `PI_MODE` (`review|edit|full`, default `review`)
- `PI_EDIT_PATHS` (comma-separated existing files allowed in `edit` mode)
- `PI_USER_ID` (optional user namespace for artifacts/state)
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
- `PI_PERMISSION_MODE` (`default|plan|accept_edits|dont_ask`, default `default`)
- `PI_NO_SUBAGENTS` (`true` disables `delegate_task`)
- `PI_MAX_TOOL_RESULT_CHARS` (default `24000`; larger tool results are offloaded)
- `PI_TOOL_RESULT_ARTIFACT_DIR` (default `tool-results`)
- `PI_NO_SESSION_NOTES` (`true` disables session notes)
- `PI_NO_WORK_NOTES` (`true` disables structured work notes)
- `PI_WORK_NOTE_ARTIFACT_DIR` (default `work-notes`)
- `PI_NO_WORK_NOTE_AUTO_UPDATE` (`true` disables automatic worklog append)
- `PI_HOOKS_CONFIG` (default `pi_hooks.json`)

### Local Bedrock Runtime

Set all three variables to route both the main and compaction models through Bedrock's OpenAI-compatible Chat Completions API:

```powershell
$env:LOCAL_BEDROCK_BASE_URL="https://bedrock-runtime.ap-northeast-1.amazonaws.com"
$env:LOCAL_BEDROCK_MODEL_ID="openai.gpt-oss-120b-1:0"
$env:LOCAL_BEDROCK_API_KEY="<your Bedrock API key>"
python openclaw_pi_langchain.py "Hello"
```

Pi validates the HTTPS Bedrock Runtime host and appends `/openai/v1` when the root endpoint is provided. Never commit the API key.

## Core CLI Flags

- `--user-id <id>`: isolate artifacts/session/audit/memory per user
- `--session <id>`: separate conversation history
- `--workspace <path>`: set the workspace root
- `--plan-mode on|off`: read-only planning mode
- `--permission-mode default|plan|accept_edits|dont_ask`: runtime permission mode
- `--no-subagents`: disable `delegate_task`
- `--max-tool-result-chars <n>`: preview limit before tool-result offload
- `--tool-result-artifact-dir <path>`: artifact subdirectory for long tool results
- `--no-session-notes`: disable session note writes
- `--no-work-notes`: disable structured work note tools and auto updates
- `--work-note-artifact-dir <path>`: artifact subdirectory for work notes
- `--no-work-note-auto-update`: disable automatic worklog updates after each run
- `--allow-tool <name>` / `--deny-tool <name>`: restrict tools for this run
- `--blocked-path <pattern>`: add blocked path patterns

## User-Isolated Artifacts

When `PI_USER_ID` or `--user-id` is set, Pi enforces isolated artifact paths:

- `reports/**`, `artifacts/**`, `outputs/**` are rewritten under:
  - `artifacts/users/<user_id>/...`
- In addition, `write` for new ad-hoc paths (for example `time_series_data.csv`, `plot.py`) is forced to:
  - `artifacts/users/<user_id>/workspace/<original-relative-path>`
- Top-level filenames (for example `foo.csv`, `script.py`) are always forced into user artifact workspace in user mode, even if a same-name file already exists in workspace root.
- Cross-user artifact paths (e.g. `artifacts/users/<other_id>/...`) are blocked.
- Session/audit/memory stores are namespaced per user:
  - `<session_dir>/users/<user_id>/...`
  - `<audit_dir>/users/<user_id>/...`
  - `<memory_dir>/users/<user_id>/...`

This keeps outputs from different users separated by default.

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
  - blocked tools: `write`, `edit`, `multiedit`, `exec`, `exec_readonly`, `memory_store`, `work_note_update`
  - allowed tools (subject to existing policy): `read`, `ls`, `find`, `grep`, `memory_search`, `memory_get`, `work_note_read`, `work_note_search`, `plan_note_write`
- Final plans are guided toward `<proposed_plan>...</proposed_plan>` with goal, critical files, steps, tests, and assumptions.
- `plan_note_write` can save the plan into `artifacts/users/<user>/work-notes/<session>.md`.
- Skill precheck failures are relaxed in plan mode so Pi can still return a plan response.
- Legacy auto-memory write is disabled while plan mode is on.
- Disable with `/plan off` or `--plan-mode off`.

## Subagents and Read-only Execution

- `delegate_task(description, prompt, agent_type)` delegates bounded work to a read-only subagent.
- Supported types: `explore`, `plan`, `verify`.
- Subagents can read/search/verify and inspect work notes, but cannot `write`, `edit`, `multiedit`, `exec`, `memory_store`, `work_note_update`, or delegate again.
- `exec_readonly(command, cwd, timeout_s)` runs only commands classified as read-only.
- Large `read`, `grep`, `work_note_search`, `exec`, and `web_fetch` results return a preview plus `full_result_path`; the complete output is saved as an artifact.

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
