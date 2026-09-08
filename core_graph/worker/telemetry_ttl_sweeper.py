"""telemetry_ttl_sweeper — periodic retention sweep of graph_run_events.

Durable tables are the source of truth after a restart; in-memory KPIs (the
collector's minute buckets) are per-process and reset on every deploy. This
worker only bounds long-term Postgres growth for graph_run_events (the
feature="graph" telemetry axis, core_049_telemetry_feature_and_graph_runs) —
it never touches tool_call_events (kept for existing dashboards/audits) or
any plugin-owned table (portfolio_ask_turns has its own sweeper — see
plugins/portfolio_plugin/ask/ttl_sweeper.py — never core reaching into it).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from core_graph.worker.worker_loop import BackgroundWorker
from db_layer.connection import get_async_session
from db_layer.models.telemetry import GraphRunEvent

logger = logging.getLogger("whiskers")

_DEFAULT_INTERVAL_SECONDS = 3600.0
_DEFAULT_RETENTION_DAYS = 30.0
_STOP_TIMEOUT = 10.0
_BATCH = 5000
_MAX_BATCHES_PER_TICK = 1


def _cfg() -> dict:
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    return SCHEDULED_JOBS_CONFIG.get("telemetry_sweep", {})


def _interval_seconds() -> float:
    return float(_cfg().get("interval_seconds", _DEFAULT_INTERVAL_SECONDS))


def _retention_days() -> float:
    return float(_cfg().get("retention_days", _DEFAULT_RETENTION_DAYS))


async def _tick() -> bool:
    """Delete stale graph_run_events in bounded batches.

    One tick deletes at most ``_BATCH`` rows so a multi-day backlog cannot
    become a single table-locking transaction. Returning True when a batch
    landed tells BackgroundWorker to loop immediately instead of idling
    a full interval per chunk.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=_retention_days())
    deleted_any = False
    try:
        async with get_async_session() as session:
            for _ in range(_MAX_BATCHES_PER_TICK):
                id_subq = (
                    select(GraphRunEvent.id)
                    .where(GraphRunEvent.created_at < cutoff)
                    .order_by(GraphRunEvent.id)
                    .limit(_BATCH)
                    .subquery()
                )
                result = await session.execute(
                    delete(GraphRunEvent).where(
                        GraphRunEvent.id.in_(select(id_subq.c.id))
                    )
                )
                await session.commit()
                if not result.rowcount:
                    break
                deleted_any = True
    except Exception:
        logger.exception("telemetry_ttl_sweeper: tick failed")
        return False
    return deleted_any


_WORKER = BackgroundWorker(
    "telemetry_ttl_sweeper",
    _tick,
    interval=_interval_seconds,
    logger=logger,
    stop_timeout=_STOP_TIMEOUT,
)


def run() -> asyncio.Task:
    """Start the worker as a background task. Idempotent while active."""
    return _WORKER.start()


async def stop() -> None:
    """Signal the worker to exit; await completion with force-cancel fallback."""
    await _WORKER.stop()


def _enabled() -> bool:
    return _cfg().get("enabled", True)


def register(registry) -> None:
    """Register telemetry_ttl_sweeper on the WorkerRegistry (default enabled)."""
    from core_graph.worker.worker_registry import WorkerSpec
    registry.register(
        WorkerSpec(
            name="telemetry_ttl_sweeper",
            run=run,
            stop=stop,
            enabled_check=_enabled,
        )
    )
