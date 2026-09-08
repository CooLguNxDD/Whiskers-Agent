"""Shared graph bootstrap: checkpointer, session locks, compiled graph singleton."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger("whiskers.core_graph.runtime")

_compiled_graph = None
_graph_lock = asyncio.Lock()
_pg_pool = None
_pg_saver = None

_session_locks: dict[str, asyncio.Lock] = {}
_session_locks_lock = asyncio.Lock()


@asynccontextmanager
async def session_lock(session_id: str | None):
    """Serialize concurrent turns for a session_id; no-op when session_id is None."""
    if not session_id:
        yield
        return

    async with _session_locks_lock:
        if session_id not in _session_locks:
            _session_locks[session_id] = asyncio.Lock()
        lock = _session_locks[session_id]

    async with lock:
        yield


async def get_checkpointer():
    """Lazy-init Postgres checkpointer pool; returns None when DATABASE_URL unset."""
    global _pg_pool, _pg_saver
    if _pg_saver is not None:
        return _pg_saver
    db_url = os.environ.get("DATABASE_URL")
    if not db_url or not db_url.strip():
        return None
    conninfo = db_url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        _pg_pool = AsyncConnectionPool(
            conninfo=conninfo,
            max_size=10,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
        await _pg_pool.open()
        _pg_saver = AsyncPostgresSaver(_pg_pool)
        await _pg_saver.setup()
        return _pg_saver
    except Exception as exc:
        logger.warning("Failed to setup Postgres checkpointer: %s", exc, exc_info=True)
        _pg_saver = None
        return None


async def shutdown_checkpointer() -> None:
    """Close the Postgres checkpointer pool and drop the compiled graph cache."""
    global _pg_pool, _pg_saver, _compiled_graph
    if _pg_pool is not None:
        try:
            await _pg_pool.close()
        except Exception as exc:
            logger.warning(
                "Error during Postgres checkpointer pool shutdown: %s",
                exc,
                exc_info=True,
            )
    _pg_pool = None
    _pg_saver = None
    _compiled_graph = None


def thread_config(session_id: str | None) -> dict:
    """LangGraph config with thread_id and recursion_limit."""
    thread_id = session_id or f"ephemeral-{uuid.uuid4()}"
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 200}


async def evict_ephemeral_thread(thread_id: str | None) -> None:
    """Delete checkpoint state for one-shot ephemeral threads; keep real sessions."""
    if not thread_id or not thread_id.startswith("ephemeral-"):
        return
    saver = _pg_saver
    if saver is None:
        return
    try:
        await saver.adelete_thread(thread_id)
    except Exception as exc:
        logger.debug("ephemeral thread cleanup skipped for %s: %s", thread_id, exc)


_SWEEP_BATCH_LIMIT = 500
_SWEEP_YIELD_EVERY = 50


async def sweep_stale_ephemeral_threads(older_than_hours: float = 24.0) -> int:
    """Delete ``ephemeral-%`` threads whose newest checkpoint is older than the cutoff.

    Covers the abort/timeout/restart paths that skip ``evict_ephemeral_thread`` on
    the clean-exit route, so one-shot threads don't accumulate in the checkpointer
    forever. The checkpoint row has no timestamp column — LangGraph stores it in
    the checkpoint JSON's ``ts`` field — so the cutoff compares against
    ``MAX(checkpoint->>'ts')`` per thread.

    Bounded to ``_SWEEP_BATCH_LIMIT`` threads per call and yields to the event loop
    every ``_SWEEP_YIELD_EVERY`` deletes — a backstop worker, so a large backlog
    (e.g. eviction breaking elsewhere) clears over a few hourly ticks instead of
    one call issuing thousands of sequential deletes without yielding.
    """
    saver = await get_checkpointer()
    if saver is None or _pg_pool is None:
        return 0
    try:
        async with _pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT thread_id FROM checkpoints
                    WHERE thread_id LIKE 'ephemeral-%%'
                    GROUP BY thread_id
                    HAVING MAX((checkpoint->>'ts')::timestamptz)
                        < now() - (%s * interval '1 hour')
                    LIMIT %s
                    """,
                    (older_than_hours, _SWEEP_BATCH_LIMIT),
                )
                rows = await cur.fetchall()
    except Exception as exc:
        logger.warning("checkpoint_sweeper: query for stale ephemeral threads failed: %s", exc)
        return 0

    swept = 0
    for row in rows:
        thread_id = row["thread_id"] if isinstance(row, dict) else row[0]
        try:
            await saver.adelete_thread(thread_id)
            swept += 1
            if swept % _SWEEP_YIELD_EVERY == 0:
                await asyncio.sleep(0)
        except Exception as exc:
            logger.debug("checkpoint_sweeper: delete failed for %s: %s", thread_id, exc)
    if swept:
        logger.info("checkpoint_sweeper: swept %d stale ephemeral thread(s)", swept)
    return swept


async def get_compiled_graph():
    """Build the dynamic LangGraph orchestrator on first call (thread-safe)."""
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph

    async with _graph_lock:
        if _compiled_graph is not None:
            return _compiled_graph

        from core.context import route_registry
        from core.llm_config_service import get_graph_core_llm
        from core_graph import build_dynamic_graph
        from core_graph.node.helpers.args import _build_context_params

        llm = await get_graph_core_llm()
        api_url = os.environ.get("PLUGIN_API_URL", "")
        context_params = _build_context_params()
        checkpointer = await get_checkpointer()

        _compiled_graph = await asyncio.to_thread(
            build_dynamic_graph,
            llm,
            context_params=context_params,
            api_url=api_url,
            route_registry=route_registry,
            checkpointer=checkpointer,
        )
        logger.info("core_graph.build_dynamic_graph compiled (headless mode)")

    return _compiled_graph


def invalidate_graph() -> None:
    """Drop the compiled-graph cache so the next run rebuilds it."""
    global _compiled_graph
    _compiled_graph = None
    try:
        from core_graph.goap_agent.node_runner import invalidate_runtime

        invalidate_runtime()
    except Exception as exc:
        logger.debug("invalidate_runtime skipped: %s", exc)
    try:
        from core_graph.model_roles.resolver import invalidate_pool_snapshot

        invalidate_pool_snapshot()
    except Exception as exc:
        logger.debug("model_roles pool snapshot invalidation skipped: %s", exc)
    logger.info("Dynamic graph cache invalidated — will rebuild on next run_graph")


def peek_compiled_graph() -> Any:
    """Return the cached graph without building (tests / diagnostics)."""
    return _compiled_graph
