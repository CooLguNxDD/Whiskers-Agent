"""artifact_sweeper — retention sweep of artifact_links rows and their MinIO objects.

``offload_text`` (core/artifact_store/store.py) writes one ``artifact_links`` row plus one
object in ``ARTIFACT_BUCKET`` on every GOAP round_summary offload, and nothing ever removed
either — the store exposes create/get/list/delete-by-short-id only, and MinIO carries no
lifecycle policy. Both Postgres and object storage grew unbounded. This worker is the
backstop, mirroring telemetry_ttl_sweeper's shape.

Only buckets on the ``scheduled_jobs.artifact_sweep.buckets`` allowlist (default:
``ARTIFACT_BUCKET``) are swept. Other producers write long-lived objects through the same
table — portfolio render assets (``ASSET_BUCKET``, referenced by baked ``?j=`` layouts) and
job_search resume PDFs (``job-search-resumes``, referenced by ``JobApplication``) — and must
never be reaped by a retention window meant for ephemeral GOAP blobs.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from core.artifact_store.minio_client import minio_available, remove_object
from core_graph.worker.worker_loop import BackgroundWorker
from db_layer.connection import get_async_session
from db_layer.models.artifacts import ArtifactLink

logger = logging.getLogger("whiskers")

_DEFAULT_INTERVAL_SECONDS = 3600.0
_DEFAULT_RETENTION_HOURS = 720.0  # 30 days
_STOP_TIMEOUT = 10.0
# Deliberately far below telemetry's 5000: every row here costs a MinIO round-trip.
_BATCH = 500
# S3 delete is idempotent, but a proxy/older gateway may still surface these.
_ABSENT_CODES = {"NoSuchKey", "NoSuchBucket"}


def _cfg() -> dict:
    from utils.server_config import SCHEDULED_JOBS_CONFIG
    return SCHEDULED_JOBS_CONFIG.get("artifact_sweep", {})


def _interval_seconds() -> float:
    return float(_cfg().get("interval_seconds", _DEFAULT_INTERVAL_SECONDS))


def _retention_hours() -> float:
    return float(_cfg().get("retention_hours", _DEFAULT_RETENTION_HOURS))


def _buckets() -> list[str]:
    """Buckets this sweeper is allowed to delete from. Never defaults to 'all'."""
    from core.artifact_store.store import ARTIFACT_BUCKET
    configured = _cfg().get("buckets")
    if not configured:
        return [ARTIFACT_BUCKET]
    return [str(b) for b in configured if str(b).strip()]


async def _drop_object(bucket: str, object_key: str) -> bool:
    """Delete one object. True when it is gone (removed or already absent).

    False means a hard failure: the caller keeps the row so the link never outlives
    its object, and the next tick retries.
    """
    try:
        # remove_object is a blocking sync MinIO call.
        await asyncio.to_thread(remove_object, bucket, object_key)
        return True
    except Exception as exc:
        if getattr(exc, "code", None) in _ABSENT_CODES:
            return True
        logger.warning("artifact_sweeper: failed to remove %s/%s: %s", bucket, object_key, exc)
        return False


async def _tick() -> bool:
    """Delete stale artifact_links rows and their objects, in bounded batches.

    One tick handles at most ``_BATCH`` rows; returning True when the batch was full
    tells BackgroundWorker to loop immediately so a backlog drains without one
    table-locking transaction.
    """
    try:
        # A MinIO outage must not delete rows pointing at objects that still exist.
        if not await asyncio.to_thread(minio_available):
            logger.debug("artifact_sweeper: MinIO unavailable, skipping tick")
            return False

        buckets = _buckets()
        if not buckets:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_retention_hours())

        async with get_async_session() as session:
            result = await session.execute(
                select(ArtifactLink.id, ArtifactLink.bucket, ArtifactLink.object_key)
                .where(
                    ArtifactLink.created_at < cutoff,
                    ArtifactLink.bucket.in_(buckets),
                )
                .order_by(ArtifactLink.id)
                .limit(_BATCH)
            )
            rows = list(result.all())
            if not rows:
                return False

            deletable = [
                row_id
                for row_id, bucket, object_key in rows
                if await _drop_object(bucket, object_key)
            ]
            if deletable:
                await session.execute(
                    delete(ArtifactLink).where(ArtifactLink.id.in_(deletable))
                )
                await session.commit()
        return len(rows) >= _BATCH
    except Exception:
        logger.exception("artifact_sweeper: tick failed")
        return False


_WORKER = BackgroundWorker(
    "artifact_sweeper",
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
    """Register artifact_sweeper on the WorkerRegistry (default enabled)."""
    from core_graph.worker.worker_registry import WorkerSpec
    registry.register(
        WorkerSpec(
            name="artifact_sweeper",
            run=run,
            stop=stop,
            enabled_check=_enabled,
        )
    )
