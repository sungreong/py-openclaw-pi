#!/usr/bin/env sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root_dir"

usage() {
    cat <<'EOF'
Usage: ./piagent.sh [--review|--full|--edit <path>] [--session <name>] [--mcp] [--check]

Start PiAgent chat. On the host, Docker Compose is prepared automatically.
Inside the pi_agent container, PiAgent runs directly without Docker.

  --review          Read, search, and plan only (default)
  --full            Allow configured tools, including file creation and tests
  --edit <path>     Allow one focused edit in an existing file
  --session <name>  Reuse a session (default: chat-main)
  --mcp             Enable MCP connections (disabled by default)
  --check           Verify PiAgent setup without calling a model
  --help            Show this help
EOF
}

mode="review"
session="chat-main"
edit_path=""
enable_mcp="false"
check_only="false"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --review) mode="review" ;;
        --full) mode="full" ;;
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
        --mcp) enable_mcp="true" ;;
        --check) check_only="true" ;;
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

if [ "$check_only" = "true" ]; then
    exec python simple_piagent.py --workspace "$root_dir" --check
fi

set -- python chat.py --workspace "$root_dir" --session "$session" --mode "$mode"
if [ "$mode" = "edit" ]; then
    set -- "$@" --edit-path "$edit_path"
fi
if [ "$enable_mcp" != "true" ]; then
    set -- "$@" --no-mcp
fi
exec "$@"
