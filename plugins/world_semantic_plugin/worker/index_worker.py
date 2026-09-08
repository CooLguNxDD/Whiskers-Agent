"""world_index_worker — consumes world_index_jobs via WorkerRegistry.

Claim pattern mirrors embedding_worker (FOR UPDATE SKIP LOCKED).
Processes one job at a time to avoid hex insert lock storms.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from plugins.world_semantic_plugin.stores import full_index
from plugins.world_semantic_plugin.stores.job_store import (
    claim_index_jobs,
    mark_job_done,
    mark_job_failed,
)

from core_graph.worker.worker_loop import BackgroundWorker

logger = logging.getLogger("whiskers.plugins.world_semantic")

_IDLE_POLL_SECONDS = 2.0
_STOP_TIMEOUT = 15.0
_CLAIM_LIMIT = 1  # serial per worker instance



async def _process_job(job: dict[str, Any]) -> dict[str, Any]:
    world_id = job["world_id"]
    objects = job.get("objects") or []
    replace = bool(job.get("replace"))
    world_meta = job.get("world_meta") or {}
    # Optional: index body embed=false skips RAG fan-out (stored on world_meta).
    meta_for_embed = world_meta if isinstance(world_meta, dict) else {}
    embed = meta_for_embed.get("embed", meta_for_embed.get("_embed", True))
    if isinstance(embed, str):
        embed = embed.lower() not in ("0", "false", "no")
    embed = bool(embed)
    logger.info(
        "world_index_worker: job=%s world=%s objects=%s replace=%s batch=%s/%s embed=%s",
        job.get("id"),
        world_id,
        len(objects),
        replace,
        job.get("batch_index"),
        job.get("batch_total"),
        embed,
    )
    result = await full_index(
        world_id,
        objects,
        replace=replace,
        world_meta=world_meta if isinstance(world_meta, dict) else {},
    )

    if embed:
        try:
            from plugins.world_semantic_plugin.stores.embed_job_store import (
                enqueue_unity_world_embed_jobs,
            )

            embed_stats = await enqueue_unity_world_embed_jobs(
                world_id,
                objects if isinstance(objects, list) else [],
                touched_hex_ids=result.get("touched_hex_ids") or [],
                replace=replace,
            )
            result["unity_embed_jobs_enqueued"] = embed_stats.get("enqueued", 0)
            result["unity_embed"] = embed_stats
        except Exception:
            logger.exception(
                "world_index_worker: unity embed enqueue failed job=%s",
                job.get("id"),
            )
            result["unity_embed_jobs_enqueued"] = 0
            result["unity_embed_error"] = "enqueue_failed"
    else:
        result["unity_embed_jobs_enqueued"] = 0

    return result


async def _tick() -> bool:
    """Claim a batch of index jobs and process them. True when work was done.

    A claim failure propagates to the worker's error handler, which logs it and
    idles — the loop itself always survives.
    """
    jobs = await claim_index_jobs(_CLAIM_LIMIT)
    if not jobs:
        return False

    for job in jobs:
        if _WORKER.should_stop():
            break
        job_id = int(job["id"])
        try:
            result = await _process_job(job)
            await mark_job_done(job_id, result)
            logger.info(
                "world_index_worker: job=%s done upserted=%s",
                job_id,
                result.get("objects_upserted"),
            )
        except Exception as exc:
            logger.exception("world_index_worker: job=%s failed", job_id)
            try:
                await mark_job_failed(job_id, f"{type(exc).__name__}: {exc}")
            except Exception:
                logger.exception("world_index_worker: mark_failed failed for %s", job_id)
    return True


def _on_claim_error(exc: BaseException) -> None:
    """A failed claim logs and idles, exactly as the hand-rolled loop did."""
    logger.exception("world_index_worker: claim failed: %s", exc)


_WORKER = BackgroundWorker(
    "world_index_worker",
    _tick,
    interval=_IDLE_POLL_SECONDS,
    logger=logger,
    stop_timeout=_STOP_TIMEOUT,
    on_error=_on_claim_error,
)


def run() -> asyncio.Task:
    """Start the worker as a background task (idempotent)."""
    return _WORKER.start()


async def stop() -> None:
    """Signal cooperative exit and await the task."""
    await _WORKER.stop()


def register(registry) -> None:
    """Register with core WorkerRegistry."""
    from core_graph.worker.worker_registry import WorkerSpec

    registry.register(
        WorkerSpec(
            name="world_index_worker",
            run=run,
            stop=stop,
            enabled_check=None,
        )
    )
