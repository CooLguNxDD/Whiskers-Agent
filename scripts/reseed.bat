@echo off
REM Truncate all MCP tables and optionally re-run migrations.
REM
REM Usage:
REM   scripts\reseed.bat              -- truncate data only (keeps schema)
REM   scripts\reseed.bat --full       -- downgrade to base, then upgrade to head
REM   scripts\reseed.bat --help       -- show this message

if /i "%~1"=="--help" goto :usage
if /i "%~1"=="-h"     goto :usage

REM ── change to project root ────────────────────────────────────────────────────
cd /d "%~dp0.."

REM ── load .env ─────────────────────────────────────────────────────────────────
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        set "key=%%A"
        if not "%%A"=="" (
            setlocal enabledelayedexpansion
            if not "!key:~0,1!"=="#" (
                endlocal
                set "%%A=%%B"
            ) else (
                endlocal
            )
        )
    )
)

REM ── require DATABASE_URL ──────────────────────────────────────────────────────
if "%DATABASE_URL%"=="" (
    echo ERROR: DATABASE_URL is not set.
    echo        Set it in .env or run:  set DATABASE_URL=postgresql+psycopg://...
    exit /b 1
)

REM ── if running locally and host is postgres, replace with localhost ─────────────
set "DATABASE_URL=%DATABASE_URL:@postgres:=@localhost:%"
set "DATABASE_URL=%DATABASE_URL://postgres:=//localhost:%"

REM ── confirm ───────────────────────────────────────────────────────────────────
echo.
echo WARNING: This will delete ALL data from the MCP database tables.
echo DATABASE_URL: %DATABASE_URL%
echo.
set /p "CONFIRM=Type YES to continue: "
if /i not "%CONFIRM%"=="YES" (
    echo Aborted.
    exit /b 0
)

REM ── full reset: downgrade then upgrade ────────────────────────────────────────
if /i "%~1"=="--full" (
    echo.
    echo [1/2] Downgrading to base...
    alembic downgrade base
    if %ERRORLEVEL% neq 0 (
        echo ERROR: downgrade failed.
        exit /b %ERRORLEVEL%
    )

    echo.
    echo [2/2] Upgrading to head...
    alembic upgrade head
    if %ERRORLEVEL% neq 0 (
        echo ERROR: upgrade failed.
        exit /b %ERRORLEVEL%
    )

    echo.
    echo Full reset complete.
    goto :done
)

REM ── data-only truncate via psql ───────────────────────────────────────────────
REM Strip the driver prefix for psql (psql uses libpq URIs, not SQLAlchemy ones)
set "PG_URL=%DATABASE_URL%"
set "PG_URL=%PG_URL:postgresql+psycopg://=postgresql://%"
set "PG_URL=%PG_URL:postgresql+psycopg2://=postgresql://%"

echo.
echo Truncating tables: api_cache, oauth_access_tokens, oauth_clients
echo.

psql "%PG_URL%" -c "TRUNCATE TABLE api_cache, oauth_access_tokens, oauth_clients RESTART IDENTITY CASCADE;"

if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: TRUNCATE failed.
    echo        Make sure psql is installed and DATABASE_URL is reachable.
    echo        Alternatively, run:  scripts\reseed.bat --full
    exit /b %ERRORLEVEL%
)

echo.
echo Reseed complete — all table data cleared.

:done
exit /b 0

:usage
echo.
echo Usage:
echo   scripts\reseed.bat            Truncate all MCP table data (keeps schema^)
echo   scripts\reseed.bat --full     Drop all tables (downgrade base^) then recreate (upgrade head^)
echo   scripts\reseed.bat --help     Show this message
echo.
echo Requires DATABASE_URL to be set in .env or as an environment variable.
echo --full requires only alembic on PATH.
echo Data-only mode also requires psql on PATH.
exit /b 0
