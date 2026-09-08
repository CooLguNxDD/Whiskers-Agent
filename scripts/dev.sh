#!/bin/bash
# Development server with auto-restart on file changes
# Usage:
#   ./scripts/dev.sh              # Run with docker-compose (full stack with postgres)
#   ./scripts/dev.sh local        # Run locally (requires DB already running)

set -e

DEV_MODE="${1:-compose}"

if [ "$DEV_MODE" = "local" ]; then
    echo "🚀 Starting Whiskers Agent Server (local development mode)"
    echo "   Auto-restarts on file changes..."
    echo ""

    # Load .env if it exists
    if [ -f .env ]; then
        set -a
        source .env
        set +a
    fi

    # Run with watchfiles
    watchfiles --poll --delay 0.5 \
        'python whiskers_agent_mcp.py --transport http --host 0.0.0.0 --port 10000'

else
    echo "🐳 Starting Whiskers Agent Stack (Docker Compose)"
    echo "   Services: postgres, whiskers-agent (with auto-restart), pgadmin"
    echo ""
    echo "   Access:"
    echo "   - MCP Server: http://localhost:10000"
    echo "   - pgAdmin: http://localhost:5050"
    echo ""
    echo "   To stop: Ctrl+C or 'docker compose down'"
    echo ""

    # Run with both compose files (base + dev override)
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build whiskers-agent postgres pgadmin
fi
