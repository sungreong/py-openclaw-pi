#!/usr/bin/env sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root_dir"

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    exec sh "$root_dir/scripts/piagent-container.sh" --help
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker Desktop (with Docker Compose v2) is required on the host." >&2
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
exec docker compose exec pi_agent /app/scripts/piagent-container.sh "$@"
