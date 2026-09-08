@echo off
REM Development server with auto-restart on file changes
REM Usage:
REM   scripts\dev.bat              - Run with docker-compose (full stack)
REM   scripts\dev.bat local        - Run locally (requires DB running)

setlocal enabledelayedexpansion

set DEV_MODE=%1
if "%DEV_MODE%"=="" set DEV_MODE=compose

if "%DEV_MODE%"=="local" (
    echo 🚀 Starting Whiskers Agent Server (local development mode)
    echo    Auto-restarts on file changes...
    echo.

    REM Load .env if it exists
    if exist .env (
        for /f "delims=" %%x in (.env) do set "%%x"
    )

    REM Run with watchfiles
    watchfiles --poll --delay 0.5 ^
        "python whiskers_agent_mcp.py --transport http --host 0.0.0.0 --port 10000"

) else (
    echo 🐳 Starting Whiskers Agent Stack (Docker Compose)
    echo    Services: postgres, whiskers-agent (with auto-restart), pgadmin
    echo.
    echo    Access:
    echo    - MCP Server: http://localhost:10000
    echo    - pgAdmin: http://localhost:5050
    echo.
    echo    To stop: Ctrl+C or 'docker compose down'
    echo.

    REM Run with both compose files (base + dev override)
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build whiskers-agent postgres pgadmin
)

endlocal
