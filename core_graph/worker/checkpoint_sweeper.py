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


def _interval_seconds() -> float:
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    cfg = SCHEDULED_JOBS_CONFIG.get("checkpoint_sweep", {})
    return float(cfg.get("interval_seconds", _DEFAULT_INTERVAL_SECONDS))


def _stale_hours() -> float:
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    cfg = SCHEDULED_JOBS_CONFIG.get("checkpoint_sweep", {})
    return float(cfg.get("stale_hours", _DEFAULT_STALE_HOURS))


async def _tick() -> bool:
    """Sweep once, then idle — the sweep owns its own error handling."""
    from core_graph.runtime.bootstrap import sweep_stale_ephemeral_threads
    try:
        await sweep_stale_ephemeral_threads(older_than_hours=_stale_hours())
    except Exception:
        logger.exception("checkpoint_sweeper: tick failed")
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
