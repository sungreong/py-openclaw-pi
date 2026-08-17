#!/usr/bin/env sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# On the host this starts the Compose service. In the pi_agent container the
# same command must run Python directly: Docker is intentionally unavailable.
if [ -f /.dockerenv ] || [ "${PIAGENT_IN_CONTAINER:-}" = "true" ]; then
    exec sh "$root_dir/scripts/piagent-container.sh" "$@"
fi

exec sh "$root_dir/scripts/piagent-host.sh" "$@"
