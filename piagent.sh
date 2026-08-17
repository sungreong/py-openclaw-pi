#!/usr/bin/env sh
set -eu

usage() {
    cat <<'EOF'
Usage: ./piagent.sh [--review|--full|--edit <path>] [--session <name>] [--mcp] [--check]

Start PiAgent chat through Docker Compose.

  --review          Read, search, and plan only (default)
  --full            Allow configured tools, including file creation and tests
  --edit <path>     Allow one focused edit in an existing file
  --session <name>  Reuse a session (default: chat-main)
  --mcp             Enable MCP connections (disabled by default)
  --check           Verify the Docker/PiAgent setup without calling a model
  --help            Show this help

The first run creates .env from .env.example when needed, then stops so you
can add an OpenAI or Local Bedrock credential without exposing it in Git.
EOF
}

mode="review"
session="chat-main"
edit_path=""
enable_mcp="false"
check_only="false"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --review)
            mode="review"
            ;;
        --full)
            mode="full"
            ;;
        --edit)
            shift
            if [ "$#" -eq 0 ]; then
                echo "Error: --edit requires an existing workspace-relative path." >&2
                exit 2
            fi
            mode="edit"
            edit_path="$1"
            ;;
        --session)
            shift
            if [ "$#" -eq 0 ]; then
                echo "Error: --session requires a name." >&2
                exit 2
            fi
            session="$1"
            ;;
        --mcp)
            enable_mcp="true"
            ;;
        --check)
            check_only="true"
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker Desktop (with Docker Compose v2) is required." >&2
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "Error: Docker Compose v2 is not available." >&2
    exit 1
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example." >&2
    echo "Add OPENAI_API_KEY or the three Local Bedrock variables, then run this command again." >&2
    exit 2
fi

docker compose up -d --build pi_agent >/dev/null

if [ "$check_only" = "true" ]; then
    exec docker compose exec pi_agent python simple_piagent.py --workspace /app --check
fi

if [ "$enable_mcp" = "true" ]; then
    if [ "$mode" = "edit" ]; then
        exec docker compose exec pi_agent python chat.py --workspace /app --session "$session" --mode edit --edit-path "$edit_path"
    fi
    exec docker compose exec pi_agent python chat.py --workspace /app --session "$session" --mode "$mode"
fi

if [ "$mode" = "edit" ]; then
    exec docker compose exec pi_agent python chat.py --workspace /app --session "$session" --mode edit --edit-path "$edit_path" --no-mcp
fi
exec docker compose exec pi_agent python chat.py --workspace /app --session "$session" --mode "$mode" --no-mcp
