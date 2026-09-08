@echo off
REM Run Alembic database migrations.
REM
REM Usage:
REM   scripts\migrate.bat                    -- upgrade to head (default)
REM   scripts\migrate.bat upgrade head       -- same as above
REM   scripts\migrate.bat upgrade <rev_id>   -- upgrade to specific revision
REM   scripts\migrate.bat downgrade -1       -- downgrade one step
REM   scripts\migrate.bat downgrade base     -- downgrade everything
REM   scripts\migrate.bat current            -- show current revision
REM   scripts\migrate.bat history            -- show migration history

setlocal enabledelayedexpansion

REM ── change to project root (parent of scripts\) ──────────────────────────────
cd /d "%~dp0.."

REM ── load .env if it exists ────────────────────────────────────────────────────
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        set "key=%%A"
        if defined key (
            if not "!key:~0,1!"=="#" set "%%A=%%B"
        )
    )
)

REM ── require DATABASE_URL ──────────────────────────────────────────────────────
REM Note: DATABASE_URL from .env is optional when running in Docker
REM The script will use Docker-internal URL by default

REM ── Docker container name ────────────────────────────────────────────────────
if "%CONTAINER_NAME%"=="" set "CONTAINER_NAME=whiskers-agent-server"

REM ── Set Docker-internal database URL ─────────────────────────────────────────
if "%DB_URL%"=="" set "DB_URL=postgresql+psycopg://whiskers:whiskers@postgres:5432/whiskers_mcp"

REM ── dispatch ──────────────────────────────────────────────────────────────────
set "CMD=%~1"
set "ARG=%~2"

if /i "%CMD%"=="current"   goto :current
if /i "%CMD%"=="history"   goto :history
if /i "%CMD%"=="downgrade" goto :downgrade
REM default: upgrade (handles "upgrade <rev>" or no args)

:upgrade
if "%ARG%"=="" set "ARG=head"
if /i "%CMD%"=="upgrade" (
    REM ARG already set from %~2
) else if not "%CMD%"=="" (
    REM first arg is not a keyword — treat it as a revision
    set "ARG=%CMD%"
)
echo Upgrading to: %ARG%
docker exec %CONTAINER_NAME% env DATABASE_URL=%DB_URL% alembic upgrade %ARG%
goto :done

:downgrade
if "%ARG%"=="" (
    echo ERROR: downgrade requires a target revision, e.g.:  migrate.bat downgrade -1
    exit /b 1
)
echo Downgrading to: %ARG%
docker exec %CONTAINER_NAME% env DATABASE_URL=%DB_URL% alembic downgrade %ARG%
goto :done

:current
docker exec %CONTAINER_NAME% env DATABASE_URL=%DB_URL% alembic current
goto :done

:history
docker exec %CONTAINER_NAME% env DATABASE_URL=%DB_URL% alembic history --verbose
goto :done

:done
if %ERRORLEVEL% neq 0 (
    echo.
    echo Migration failed with exit code %ERRORLEVEL%.
    exit /b %ERRORLEVEL%
)
echo.
echo Migration completed successfully.
