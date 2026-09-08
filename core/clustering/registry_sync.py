"""
Cross-process tool-visibility sync via Postgres LISTEN/NOTIFY (Phase 7 modularity refactor).

When multiple server processes share one Postgres database, disabling/enabling a tool via
ToolVisibility on one process should propagate live to the others. This module opens a single
dedicated psycopg3 AsyncConnection (NOT pooled — LISTEN requires a connection that is never
recycled) per process, issues ``LISTEN tool_visibility_changed``, and applies each received
notification to the LOCAL FastMCP visibility transform via the fastmcp_adapter directly (bypassing
ToolVisibility.hide()/show() to avoid re-broadcasting what we just received — an echo would create
an infinite notify storm across nodes).

Inert (returns None / no-ops) whenever ``core.context._DB_AVAILABLE`` is False, matching every
other DB-conditional code path in this codebase.
"""
import asyncio
import json
import logging

logger = logging.getLogger("whiskers")

_CHANNEL = "tool_visibility_changed"
_MAX_CONSECUTIVE_FAILURES = 50
_DEGRADED_RETRY_INTERVAL_S = 300.0

_listener_task: asyncio.Task | None = None
_listener_conn = None  # psycopg.AsyncConnection, kept alive for the process lifetime


def _bare_dsn(url: str) -> str:
    """Strip the SQLAlchemy dialect suffix (+psycopg / +psycopg2) for a raw psycopg connect()."""
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url[len("postgresql+psycopg2://"):]
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    return url


async def broadcast_tool_visibility_change(action: str, tool_name: str) -> None:
    """Fire a NOTIFY so every other process sharing this DB applies the same change.

    No-op when DB is unavailable. Failures are logged, never raised — a broadcast failure must
    never break the local hide/show call that triggered it.
    """
    from core.context._env import _DB_AVAILABLE
    if not _DB_AVAILABLE:
        return
    try:
        from db_layer.connection import get_async_session
        from sqlalchemy import text
        payload = json.dumps({"action": action, "tool_name": tool_name})
        async with get_async_session() as session:
            await session.execute(text("SELECT pg_notify(:channel, :payload)"),
                                   {"channel": _CHANNEL, "payload": payload})
            await session.commit()
    except Exception as exc:
        logger.warning("registry_sync: broadcast failed for %s(%r): %s", action, tool_name, exc)


def _apply_remote_change(payload: str) -> None:
    """Apply a received notification to the LOCAL FastMCP visibility transform only.

    Deliberately bypasses ToolVisibility.hide()/show() (which would re-broadcast) and calls the
    fastmcp_adapter functions directly against the bound mcp app, mirroring exactly what
    ToolVisibility.hide()/show() do internally minus the broadcast.
    """
    try:
        data = json.loads(payload)
        action = data.get("action")
        tool_name = data.get("tool_name")
        if not action or not tool_name:
            return
        from core.context import tool_visibility
        if tool_visibility._mcp is None:
            return
        from core.proxy_tools.fastmcp_adapter import disable_tool, enable_tool
        if action == "hide":
            disable_tool(tool_visibility._mcp, {tool_name}, {"tool"})
            logger.info("registry_sync: applied remote hide for '%s'", tool_name)
        elif action == "show":
            enable_tool(tool_visibility._mcp, {tool_name}, {"tool"})
            logger.info("registry_sync: applied remote show for '%s'", tool_name)
    except Exception as exc:
        logger.warning("registry_sync: failed to apply remote change (payload=%r): %s", payload, exc)


async def _listen_loop(conn) -> None:
    """Consume notifications on ``_CHANNEL`` until cancelled with reconnect resilience."""
    import psycopg
    from psycopg import sql
    from db_layer.connection import get_database_url

    global _listener_conn
    dsn = _bare_dsn(get_database_url())
    backoff = 1.0
    consecutive_failures = 0

    while True:
        try:
            if conn is None:
                # Reconnect
                conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
                await conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(_CHANNEL)))
                _listener_conn = conn
                logger.info("registry_sync: reconnected and LISTEN '%s' active", _CHANNEL)
                backoff = 1.0
                consecutive_failures = 0

            async for notify in conn.notifies():
                _apply_remote_change(notify.payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            consecutive_failures += 1
            if consecutive_failures > _MAX_CONSECUTIVE_FAILURES:
                logger.critical(
                    "registry_sync: %d consecutive connection failures — cross-process "
                    "tool-visibility sync is degraded for this process, retrying every %.0fs: %s",
                    consecutive_failures, _DEGRADED_RETRY_INTERVAL_S, exc,
                )
                if conn is not None:
                    try:
                        await conn.close()
                    except Exception:
                        logger.debug("registry_sync: error closing connection on giveup", exc_info=True)
                    conn = None
                _listener_conn = None
                # Don't spin the fast exponential backoff forever, but don't die
                # permanently either — a transient extended outage (DB maintenance
                # window) should self-heal without requiring a container restart.
                await asyncio.sleep(_DEGRADED_RETRY_INTERVAL_S)
                consecutive_failures = 0
                backoff = 1.0
                continue
            logger.warning("registry_sync: listener connection lost, retrying in %.1fs: %s", backoff, exc)
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    logger.debug("registry_sync: error closing lost listener connection", exc_info=True)
                conn = None
            _listener_conn = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


async def start_registry_sync() -> asyncio.Task | None:
    """Open the dedicated LISTEN connection and start the background consume loop.

    Returns None (no-op) when the DB is unavailable. Returns the running asyncio.Task otherwise
    (also stored in the module-level ``_listener_task`` for ``stop_registry_sync`` to use without
    arguments, matching the zero-arg teardown style of ``dispose_async_engine``).
    """
    global _listener_task, _listener_conn
    if _listener_task is not None:
        return _listener_task
    from core.context._env import _DB_AVAILABLE
    if not _DB_AVAILABLE:
        return None
    try:
        import psycopg
        from psycopg import sql
        from db_layer.connection import get_database_url
        dsn = _bare_dsn(get_database_url())
        conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
        await conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(_CHANNEL)))
        _listener_conn = conn
        _listener_task = asyncio.create_task(_listen_loop(conn))
        logger.info("registry_sync: LISTEN '%s' active (cross-process tool-visibility sync)", _CHANNEL)
        return _listener_task
    except Exception as exc:
        logger.warning("registry_sync: failed to start LISTEN — cross-process sync disabled: %s", exc)
        return None


async def stop_registry_sync() -> None:
    """Cancel the listener task and close the dedicated connection. Safe to call when never started."""
    global _listener_task, _listener_conn
    if _listener_task is not None:
        _listener_task.cancel()
        try:
            await _listener_task
        except (asyncio.CancelledError, Exception):
            logger.debug("registry_sync.py: swallowed exception", exc_info=True)
        _listener_task = None
    if _listener_conn is not None:
        try:
            await _listener_conn.close()
        except Exception:
            logger.debug("registry_sync.py: swallowed exception", exc_info=True)
        _listener_conn = None
