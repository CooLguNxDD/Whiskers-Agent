"""ttl_sweeper — periodic retention sweep of portfolio_ask_turns.

Plugin-owned counterpart to core_graph/worker/telemetry_ttl_sweeper.py (core
never reaches into a plugin's table). Self-registered from
plugin_config.py::PortfolioPlugin.on_ready, mirroring discovery/worker.py.
"""

from __future__ import annotations

import asyncio
import logging

from core_graph.worker.worker_loop import BackgroundWorker

logger = logging.getLogger("whiskers.plugins.portfolio.ask.ttl_sweeper")

_DEFAULT_INTERVAL_SECONDS = 3600.0
_DEFAULT_RETENTION_DAYS = 30.0
_STOP_TIMEOUT = 10.0
_BATCH = 5000
_MAX_BATCHES_PER_TICK = 1


def _cfg() -> dict:
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    return SCHEDULED_JOBS_CONFIG.get("portfolio_ask_turns_sweep", {})


def _interval_seconds() -> float:
    return float(_cfg().get("interval_seconds", _DEFAULT_INTERVAL_SECONDS))


async def _tick() -> bool:
    """Delete stale portfolio_ask_turns in bounded batches.

    Mirrors core telemetry_ttl_sweeper: one ``_BATCH`` per tick, True when
    a batch landed so BackgroundWorker drains a backlog without a 1h wait.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import delete, select

    from db_layer.connection import get_async_session
    from plugins.portfolio_plugin.models import PortfolioAskTurn

    cutoff = datetime.now(timezone.utc) - timedelta(days=float(_cfg().get("retention_days", _DEFAULT_RETENTION_DAYS)))
    deleted_any = False
    try:
        async with get_async_session() as session:
            for _ in range(_MAX_BATCHES_PER_TICK):
                id_subq = (
                    select(PortfolioAskTurn.id)
                    .where(PortfolioAskTurn.created_at < cutoff)
                    .order_by(PortfolioAskTurn.id)
                    .limit(_BATCH)
                    .subquery()
                )
                result = await session.execute(
                    delete(PortfolioAskTurn).where(
                        PortfolioAskTurn.id.in_(select(id_subq.c.id))
                    )
                )
                await session.commit()
                if not result.rowcount:
                    break
                deleted_any = True
    except Exception:
        logger.exception("portfolio_ask_turns_ttl_sweeper: tick failed")
        return False
    return deleted_any


_WORKER = BackgroundWorker(
    "portfolio_ask_turns_ttl_sweeper",
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
    """Register portfolio_ask_turns_ttl_sweeper on the WorkerRegistry (default enabled)."""
    from core_graph.worker.worker_registry import WorkerSpec
    registry.register(
        WorkerSpec(
            name="portfolio_ask_turns_ttl_sweeper",
            run=run,
            stop=stop,
            enabled_check=_enabled,
        )
    )
