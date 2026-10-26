"""checkpoint_sweeper — periodic sweep of stale ``ephemeral-`` LangGraph threads.

``evict_ephemeral_thread`` (core_graph/runtime/bootstrap.py) only runs on the clean
exit path; an abort, timeout, or container restart leaves the thread's checkpoint
rows behind indefinitely. This worker is the backstop.
"""

from __future__ import annotations

import asyncio
import logging

from core_graph.worker.worker_loop import BackgroundWorker

logger = logging.getLogger("whiskers")

_DEFAULT_INTERVAL_SECONDS = 3600.0
_DEFAULT_STALE_HOURS = 24.0
_STOP_TIMEOUT = 10.0
_COUNTS: dict[str, int] = {"checkpoints": 0, "blobs": 0, "writes": 0}
_LAST_DELETED: dict[str, int] = {"deleted": 0}
_TOTAL_DELETED = 0
_GAUGES = {
    "checkpoint_rows_checkpoints": lambda: _COUNTS["checkpoints"],
    "checkpoint_rows_blobs": lambda: _COUNTS["blobs"],
    "checkpoint_rows_writes": lambda: _COUNTS["writes"],
    "checkpoint_sweep_last_deleted": lambda: _LAST_DELETED["deleted"],
    "checkpoint_sweep_total_deleted": lambda: _TOTAL_DELETED,
}


def _register_gauges() -> None:
    from core.telemetry.collector import collector
    for key, provider in _GAUGES.items():
        collector.register_gauge_provider(key, provider)


def _unregister_gauges() -> None:
    from core.telemetry.collector import collector
    for key in _GAUGES:
        collector.unregister_gauge_provider(key)


def _interval_seconds() -> float:
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    cfg = SCHEDULED_JOBS_CONFIG.get("checkpoint_sweep", {})
    return float(cfg.get("interval_seconds", _DEFAULT_INTERVAL_SECONDS))


def _stale_hours() -> float:
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    cfg = SCHEDULED_JOBS_CONFIG.get("checkpoint_sweep", {})
    return float(cfg.get("stale_hours", _DEFAULT_STALE_HOURS))


async def _tick() -> bool:
    """Refresh cached checkpoint metrics and sweep once, then idle."""
    global _TOTAL_DELETED
    from core_graph.runtime.bootstrap import checkpoint_table_counts, sweep_stale_ephemeral_threads
    try:
        deleted = await sweep_stale_ephemeral_threads(older_than_hours=_stale_hours())
        _LAST_DELETED["deleted"] = int(deleted or 0)
        _TOTAL_DELETED += _LAST_DELETED["deleted"]
    except Exception:
        logger.exception("checkpoint_sweeper: tick failed")
    try:
        counts = await checkpoint_table_counts()
        _COUNTS.update(checkpoints=counts["checkpoints"], blobs=counts["checkpoint_blobs"], writes=counts["checkpoint_writes"])
    except Exception:
        logger.debug("checkpoint_sweeper: checkpoint count query failed", exc_info=True)
    return False


_WORKER = BackgroundWorker(
    "checkpoint_sweeper",
    _tick,
    interval=_interval_seconds,
    logger=logger,
    stop_timeout=_STOP_TIMEOUT,
)


def run() -> asyncio.Task:
    """Start the worker as a background task. Idempotent while active."""
    _register_gauges()
    return _WORKER.start()


async def stop() -> None:
    """Signal the worker to exit; await completion with force-cancel fallback."""
    try:
        await _WORKER.stop()
    finally:
        _unregister_gauges()


def _enabled() -> bool:
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    return SCHEDULED_JOBS_CONFIG.get("checkpoint_sweep", {}).get("enabled", True)


def register(registry) -> None:
    """Register checkpoint_sweeper on the WorkerRegistry (default enabled)."""
    from core_graph.worker.worker_registry import WorkerSpec
    registry.register(
        WorkerSpec(
            name="checkpoint_sweeper",
            run=run,
            stop=stop,
            enabled_check=_enabled,
        )
    )
