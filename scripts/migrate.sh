#!/usr/bin/env bash
# Run Alembic database migrations.
#
# Usage:
#   ./scripts/migrate.sh                    # upgrade to head (default)
#   ./scripts/migrate.sh upgrade head       # same as above
#   ./scripts/migrate.sh upgrade <rev_id>   # upgrade to specific revision
#   ./scripts/migrate.sh downgrade -1       # downgrade one step
#   ./scripts/migrate.sh downgrade base     # downgrade everything
#   ./scripts/migrate.sh current            # show current revision
#   ./scripts/migrate.sh history            # show migration history

set -euo pipefail

# ── change to project root (parent of scripts/) ──────────────────────────────
cd "$(dirname "$0")/.."

# ── load .env if it exists ────────────────────────────────────────────────────
if [ -f .env ]; then
    set -o allexport
    # shellcheck disable=SC1091
    source .env
    set +o allexport
fi

# ── Docker container name ─────────────────────────────────────────────────────
CONTAINER_NAME="${CONTAINER_NAME:-whiskers-agent-server}"

# ── Helper function to run command in Docker ──────────────────────────────────
run_in_docker() {
    if ! docker ps --filter "name=$CONTAINER_NAME" --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo "ERROR: Docker container '$CONTAINER_NAME' is not running."
        echo "       Start it with: docker compose up -d"
        exit 1
    fi
    # Use Docker-internal database URL (postgres service on the whiskers-network)
    local DB_URL="${DATABASE_URL:-postgresql+psycopg://whiskers:whiskers@postgres:5432/whiskers_mcp}"
    docker exec "$CONTAINER_NAME" env DATABASE_URL="$DB_URL" "$@"
}

CMD="${1:-upgrade}"
ARG="${2:-head}"

case "$CMD" in
    upgrade)
        echo "Upgrading to: $ARG"
        run_in_docker alembic upgrade "$ARG"
        ;;
    downgrade)
        if [ -z "${2:-}" ]; then
            echo "ERROR: downgrade requires a target revision, e.g.:  migrate.sh downgrade -1"
            exit 1
        fi
        echo "Downgrading to: $ARG"
        run_in_docker alembic downgrade "$ARG"
        ;;
    current)
        run_in_docker alembic current
        ;;
    history)
        run_in_docker alembic history --verbose
        ;;
    *)
        # Treat first arg as a bare revision (e.g. ./migrate.sh head)
        echo "Upgrading to: $CMD"
        run_in_docker alembic upgrade "$CMD"
        ;;
esac

echo ""
echo "Done."
