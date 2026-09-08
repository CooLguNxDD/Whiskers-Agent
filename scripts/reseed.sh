#!/usr/bin/env bash
# Truncate all MCP tables and optionally re-run migrations.
#
# Usage:
#   ./scripts/reseed.sh            # truncate data only (keeps schema)
#   ./scripts/reseed.sh --full     # downgrade to base, then upgrade to head
#   ./scripts/reseed.sh --help     # show this message

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    echo ""
    echo "Usage:"
    echo "  ./scripts/reseed.sh            Truncate all MCP table data (keeps schema)"
    echo "  ./scripts/reseed.sh --full     Drop all tables (downgrade base) then recreate (upgrade head)"
    echo "  ./scripts/reseed.sh --help     Show this message"
    echo ""
    echo "Requires DATABASE_URL in .env or environment."
    echo "--full requires only alembic on PATH."
    echo "Data-only mode also requires psql on PATH."
    exit 0
fi

# ── change to project root ────────────────────────────────────────────────────
cd "$(dirname "$0")/.."

# ── load .env ─────────────────────────────────────────────────────────────────
if [ -f .env ]; then
    set -o allexport
    # shellcheck disable=SC1091
    source .env
    set +o allexport
fi

# ── require DATABASE_URL ──────────────────────────────────────────────────────
if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL is not set."
    echo "       Set it in .env or export it before running this script."
    exit 1
fi

# ── if running locally and host is postgres, replace with localhost ─────────────
DATABASE_URL="${DATABASE_URL//@postgres:/@localhost:}"
DATABASE_URL="${DATABASE_URL//\/\/postgres:/\/\/localhost:}"

# ── confirm ───────────────────────────────────────────────────────────────────
echo ""
echo "WARNING: This will delete ALL data from the MCP database tables."
echo "DATABASE_URL: $DATABASE_URL"
echo ""
read -r -p "Type YES to continue: " CONFIRM
if [ "$CONFIRM" != "YES" ]; then
    echo "Aborted."
    exit 0
fi

# ── full reset: downgrade then upgrade ────────────────────────────────────────
if [[ "${1:-}" == "--full" ]]; then
    echo ""
    echo "[1/2] Downgrading to base..."
    alembic downgrade base

    echo ""
    echo "[2/2] Upgrading to head..."
    alembic upgrade head

    echo ""
    echo "Full reset complete."
    exit 0
fi

# ── data-only truncate via psql ───────────────────────────────────────────────
# Strip SQLAlchemy driver prefix so psql gets a plain libpq URI
PG_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"
PG_URL="${PG_URL/postgresql+psycopg2:\/\//postgresql://}"

echo ""
echo "Truncating tables: api_cache, oauth_access_tokens, oauth_clients"
echo ""

psql "$PG_URL" -c "TRUNCATE TABLE api_cache, oauth_access_tokens, oauth_clients RESTART IDENTITY CASCADE;"

echo ""
echo "Reseed complete — all table data cleared."
