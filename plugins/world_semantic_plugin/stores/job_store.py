"""Durable queue for world index batches (enqueue / claim / status)."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

from db_layer.connection import get_async_session

logger = logging.getLogger("whiskers.plugins.world_semantic")

_CLAIM_SQL = text(
    """
    WITH claimed AS (
      SELECT id FROM world_index_jobs
      WHERE status = 'pending'
      ORDER BY created_at
      FOR UPDATE SKIP LOCKED
      LIMIT :batch_size
    )
    UPDATE world_index_jobs j
    SET    status     = 'processing',
           claimed_at = now(),
           attempts   = attempts + 1
    FROM   claimed
    WHERE  j.id = claimed.id
    RETURNING j.id, j.world_id, j.replace, j.world_meta, j.objects,
              j.objects_count, j.batch_index, j.batch_total, j.client_run_id,
              j.attempts
    """
)


def _row_to_job(row) -> dict[str, Any]:
    meta = row.world_meta
    objs = row.objects
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if isinstance(objs, str):
        try:
            objs = json.loads(objs)
        except Exception:
            objs = []
    return {
        "id": int(row.id),
        "world_id": row.world_id,
        "replace": bool(row.replace),
        "world_meta": meta if isinstance(meta, dict) else {},
        "objects": objs if isinstance(objs, list) else [],
        "objects_count": int(row.objects_count or 0),
        "batch_index": row.batch_index,
        "batch_total": row.batch_total,
        "client_run_id": row.client_run_id,
        "attempts": int(row.attempts or 0),
    }


async def enqueue_index_job(
    world_id: str,
    objects: list[dict],
    *,
    replace: bool = False,
    world_meta: dict | None = None,
    batch_index: int | None = None,
    batch_total: int | None = None,
    client_run_id: str | None = None,
) -> dict[str, Any]:
    """Insert a pending index job and NOTIFY the worker. Returns job summary."""
    objects = objects if isinstance(objects, list) else []
    meta = world_meta if isinstance(world_meta, dict) else {}
    async with get_async_session() as session:
        result = await session.execute(
            text(
                """
                INSERT INTO world_index_jobs (
                    world_id, status, replace, world_meta, objects,
                    objects_count, batch_index, batch_total, client_run_id
                ) VALUES (
                    :world_id, 'pending', :replace,
                    CAST(:world_meta AS jsonb), CAST(:objects AS jsonb),
                    :objects_count, :batch_index, :batch_total, :client_run_id
                )
                RETURNING id, created_at
                """
            ),
            {
                "world_id": world_id,
                "replace": bool(replace),
                "world_meta": json.dumps(meta),
                "objects": json.dumps(objects),
                "objects_count": len(objects),
                "batch_index": batch_index,
                "batch_total": batch_total,
                "client_run_id": client_run_id,
            },
        )
        row = result.first()
        depth = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM world_index_jobs
                    WHERE status IN ('pending', 'processing')
                    """
                )
            )
        ).scalar() or 0
        await session.execute(text("NOTIFY world_index_jobs_new"))
        await session.commit()

    job_id = int(row.id)
    created = row.created_at
    return {
        "status": "queued",
        "job_id": job_id,
        "world_id": world_id,
        "objects_count": len(objects),
        "replace": bool(replace),
        "batch_index": batch_index,
        "batch_total": batch_total,
        "client_run_id": client_run_id,
        "queue_depth": int(depth),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
    }


async def claim_index_jobs(limit: int = 1) -> list[dict[str, Any]]:
    """Claim pending jobs with FOR UPDATE SKIP LOCKED."""
    if limit < 1:
        limit = 1
    async with get_async_session() as session:
        rows = (await session.execute(_CLAIM_SQL, {"batch_size": limit})).all()
        await session.commit()
    return [_row_to_job(r) for r in rows]


async def mark_job_done(job_id: int, result: dict[str, Any] | None = None) -> None:
    """Mark a world index job as successfully completed with optional result payload."""
    async with get_async_session() as session:
        await session.execute(
            text(
                """
                UPDATE world_index_jobs
                SET status = 'done',
                    result = CAST(:result AS jsonb),
                    completed_at = now(),
                    last_error = NULL
                WHERE id = :id
                """
            ),
            {
                "id": job_id,
                "result": json.dumps(result or {}),
            },
        )
        await session.commit()


async def mark_job_failed(job_id: int, error: str) -> None:
    """Mark a world index job as failed with error details and completion timestamp."""
    async with get_async_session() as session:
        await session.execute(
            text(
                """
                UPDATE world_index_jobs
                SET status = 'failed',
                    last_error = :err,
                    completed_at = now()
                WHERE id = :id
                """
            ),
            {"id": job_id, "err": (error or "")[:1000]},
        )
        await session.commit()


async def get_job(job_id: int) -> dict[str, Any] | None:
    """Fetch a world index job by its primary key ID, returning None if not found."""
    async with get_async_session() as session:
        result = await session.execute(
            text(
                """
                SELECT id, world_id, status, replace, world_meta, objects_count,
                       batch_index, batch_total, client_run_id, result, last_error,
                       attempts, created_at, claimed_at, completed_at
                FROM world_index_jobs
                WHERE id = :id
                """
            ),
            {"id": job_id},
        )
        row = result.mappings().first()
        if not row:
            return None
        return _serialize_job_row(dict(row))


async def list_jobs(
    world_id: str | None = None,
    *,
    status: str | None = None,
    client_run_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List world index jobs matching optional filters, ordered newest first."""
    limit = max(1, min(int(limit or 50), 200))
    clauses = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if world_id:
        clauses.append("world_id = :world_id")
        params["world_id"] = world_id
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if client_run_id:
        clauses.append("client_run_id = :client_run_id")
        params["client_run_id"] = client_run_id

    sql = f"""
        SELECT id, world_id, status, replace, world_meta, objects_count,
               batch_index, batch_total, client_run_id, result, last_error,
               attempts, created_at, claimed_at, completed_at
        FROM world_index_jobs
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC
        LIMIT :limit
    """
    async with get_async_session() as session:
        result = await session.execute(text(sql), params)
        rows = result.mappings().all()
    return [_serialize_job_row(dict(r)) for r in rows]


async def queue_stats(world_id: str | None = None) -> dict[str, int]:
    """Return status aggregate counts for all or a specific world's index jobs."""
    params: dict[str, Any] = {}
    where = ""
    if world_id:
        where = "WHERE world_id = :world_id"
        params["world_id"] = world_id
    async with get_async_session() as session:
        result = await session.execute(
            text(
                f"""
                SELECT status, COUNT(*) AS n
                FROM world_index_jobs
                {where}
                GROUP BY status
                """
            ),
            params,
        )
        counts = {r.status: int(r.n) for r in result}
    return {
        "pending": counts.get("pending", 0),
        "processing": counts.get("processing", 0),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
    }


def _serialize_job_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("created_at", "claimed_at", "completed_at"):
        if out.get(key) is not None and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
    # Never dump full objects payload in status APIs.
    out.pop("objects", None)
    meta = out.get("world_meta")
    if isinstance(meta, str):
        try:
            out["world_meta"] = json.loads(meta)
        except Exception:
            out["world_meta"] = {}
    result = out.get("result")
    if isinstance(result, str):
        try:
            out["result"] = json.loads(result)
        except Exception:
            logger.debug("job_store.py: swallowed exception", exc_info=True)
    return out
