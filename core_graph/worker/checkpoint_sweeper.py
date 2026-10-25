"""checkpoint_sweeper — periodic sweep of stale LangGraph checkpoint threads.

``evict_ephemeral_thread`` (core_graph/runtime/bootstrap.py) only runs on the clean
exit path; an abort, timeout, or container restart leaves the thread's checkpoint
rows behind indefinitely. This worker is the backstop for ``ephemeral-`` threads.

Session threads (``thread_id = session_id``) are swept separately and only when
``session_retention_days`` > 0; in-use (locked) and interrupted threads are skipped.
"""

from __future__ import annotations

import asyncio
import logging

from core_graph.worker.worker_loop import BackgroundWorker

logger = logging.getLogger("whiskers")

_DEFAULT_INTERVAL_SECONDS = 3600.0
_DEFAULT_STALE_HOURS = 24.0
_DEFAULT_SESSION_RETENTION_DAYS = 0.0  # 0 = session sweep disabled (opt-in)
_STOP_TIMEOUT = 10.0


def _interval_seconds() -> float:
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    cfg = SCHEDULED_JOBS_CONFIG.get("checkpoint_sweep", {})
    return float(cfg.get("interval_seconds", _DEFAULT_INTERVAL_SECONDS))


def _stale_hours() -> float:
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    cfg = SCHEDULED_JOBS_CONFIG.get("checkpoint_sweep", {})
    return float(cfg.get("stale_hours", _DEFAULT_STALE_HOURS))


def _session_retention_days() -> float:
    """Session-thread retention in days; 0 (default, or unparsable) disables the sweep."""
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    cfg = SCHEDULED_JOBS_CONFIG.get("checkpoint_sweep", {})
    try:
        return float(cfg.get("session_retention_days", _DEFAULT_SESSION_RETENTION_DAYS))
    except (TypeError, ValueError):
        return _DEFAULT_SESSION_RETENTION_DAYS


async def _tick() -> bool:
    """Sweep once, then idle — each sweep is guarded so one failing never blocks the other."""
    from core_graph.runtime import bootstrap
    try:
        await bootstrap.sweep_stale_ephemeral_threads(older_than_hours=_stale_hours())
    except Exception:
        logger.exception("checkpoint_sweeper: ephemeral sweep failed")
    retention_days = _session_retention_days()
    if retention_days > 0:
        try:
            await bootstrap.sweep_stale_session_threads(retention_days=retention_days)
        except Exception:
            logger.exception("checkpoint_sweeper: session sweep failed")
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
    return _WORKER.start()


async def stop() -> None:
    """Signal the worker to exit; await completion with force-cancel fallback."""
    await _WORKER.stop()


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
