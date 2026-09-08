"""content_sync_worker — placeholder loop for future external content ingestion.

Disabled by default via ``scheduled_jobs.content_sync.enabled``. When enabled,
the loop only idles until stop; real pull/upsert is TODO(track2).
"""

from __future__ import annotations

import asyncio
import logging

from core_graph.worker.worker_loop import BackgroundWorker

logger = logging.getLogger("whiskers")

_IDLE_POLL_SECONDS = 30.0
_STOP_TIMEOUT = 10.0


async def _tick() -> bool:
    """No-op placeholder tick — always idles."""
    # TODO(track2): pull external sources and upsert via add_content_vector
    return False


_WORKER = BackgroundWorker(
    "content_sync_worker",
    _tick,
    interval=_IDLE_POLL_SECONDS,
    logger=logger,
    stop_timeout=_STOP_TIMEOUT,
)


def run(idle_poll: float = _IDLE_POLL_SECONDS) -> asyncio.Task:
    """Start the worker as a background task. Idempotent while active."""
    return _WORKER.start(interval=idle_poll)


async def stop() -> None:
    """Signal the worker to exit; await completion with force-cancel fallback."""
    await _WORKER.stop()


def _enabled() -> bool:
    """Return True only when scheduled_jobs.content_sync.enabled is set."""
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    return SCHEDULED_JOBS_CONFIG.get("content_sync", {}).get("enabled", False)


def register(registry) -> None:
    """Register content_sync_worker on the WorkerRegistry (default disabled)."""
    from core_graph.worker.worker_registry import WorkerSpec
    registry.register(
        WorkerSpec(
            name="content_sync_worker",
            run=run,
            stop=stop,
            enabled_check=_enabled,
        )
    )
