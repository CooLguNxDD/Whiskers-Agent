"""
embedding_worker — async loop that consumes ``embedding_jobs``.

Claim semantics use the canonical CTE pattern::

    WITH claimed AS (
      SELECT id FROM embedding_jobs
      WHERE status = 'pending'
      ORDER BY created_at
      FOR UPDATE SKIP LOCKED
      LIMIT N
    )
    UPDATE embedding_jobs e
    SET status='processing', claimed_at=now(), attempts=attempts+1
    FROM claimed
    WHERE e.id = claimed.id
    RETURNING e.*;

``FOR UPDATE SKIP LOCKED`` guarantees that concurrent workers never claim the
same row. ``status='processing'`` removes claimed rows from the
``embedding_jobs_pending`` partial index so they're invisible to subsequent
scans without locking the whole table.

Cost guardrail: exactly **one** Gemini call per ``process_batch``. A failure
parks the whole batch at ``status='failed'`` with the error stored in
``last_error`` — no partial retries, no thundering herd.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import text, func, update
from sqlalchemy.dialects.postgresql import insert

from db_layer.connection import get_async_session
from db_layer.embeddings.embeddings_core import embed_batch
from db_layer.models import RouteEmbedding, EmbeddingJob

from core_graph.worker.worker_loop import BackgroundWorker

logger = logging.getLogger("whiskers")


_BATCH_SIZE = 100
_IDLE_POLL_SECONDS = 5.0

# Module-level handle so the server-shutdown teardown can cancel cleanly.
_STOP_TIMEOUT = 10.0  # seconds to wait for cooperative exit before force-cancel


_CLAIM_SQL = text("""
    WITH claimed AS (
      SELECT id FROM embedding_jobs
      WHERE status = 'pending'
      ORDER BY created_at
      FOR UPDATE SKIP LOCKED
      LIMIT :batch_size
    )
    UPDATE embedding_jobs e
    SET    status     = 'processing',
           claimed_at = now(),
           attempts   = attempts + 1
    FROM   claimed
    WHERE  e.id = claimed.id
    RETURNING e.id, e.plugin_id, e.operation_id, e.content_hash, e.payload
""")


async def _claim_batch(batch_size: int) -> list[dict[str, Any]]:
    async with get_async_session() as session:
        rows = (await session.execute(
            _CLAIM_SQL, {"batch_size": batch_size},
        )).all()
        await session.commit()
    return [
        {
            "id": r.id,
            "plugin_id": r.plugin_id,
            "operation_id": r.operation_id,
            "content_hash": r.content_hash,
            "payload": r.payload,
        }
        for r in rows
    ]


async def _mark_failed(job_ids: list[int], error: str) -> None:
    async with get_async_session() as session:
        stmt = (
            update(EmbeddingJob)
            .where(EmbeddingJob.id.in_(job_ids))
            .values(status="failed", last_error=(error or "")[:500])
        )
        await session.execute(stmt)
        await session.commit()


def _route_embedding_meta(payload: dict[str, Any]) -> dict[str, Any]:
    """Build route_embeddings.meta so workspace survives hybrid-search → tool cards."""
    tags = [t for t in (payload.get("tags") or []) if isinstance(t, str)]
    ws = payload.get("workspace_label")
    if isinstance(ws, str) and ws.strip():
        ws = ws.strip()
        tag = f"workspace:{ws}"
        if tag not in tags:
            tags.append(tag)
        return {"tags": tags, "workspace_label": ws}
    return {"tags": tags}


def _build_route_embedding_stmt(job: dict[str, Any], vec: list[float], model_id: str) -> Any:
    payload = job["payload"] or {}
    doc_text = job.get("doc_text") or ""
    meta = _route_embedding_meta(payload)
    return (
        insert(RouteEmbedding)
        .values(
            plugin_id=job["plugin_id"],
            operation_id=job["operation_id"],
            content_hash=job["content_hash"],
            doc_text=doc_text,
            description=payload.get("description", ""),
            parameters=payload.get("parameters") or {},
            method=payload.get("method", "") or "",
            path_template=payload.get("path_template", "") or "",
            path=payload.get("path_template", "") or "",  # legacy NOT NULL column
            is_fast_path=bool(payload.get("is_fast_path", False)),
            meta=meta,
            embedding=vec,
            model=model_id,
            embedded_at=text("now()"),
        )
        .on_conflict_do_update(
            constraint="route_embeddings_plugin_op_unique",
            set_=dict(
                content_hash=job["content_hash"],
                doc_text=doc_text,
                description=payload.get("description", ""),
                parameters=payload.get("parameters") or {},
                method=payload.get("method", "") or "",
                path_template=payload.get("path_template", "") or "",
                path=payload.get("path_template", "") or "",
                is_fast_path=bool(payload.get("is_fast_path", False)),
                meta=meta,
                embedding=vec,
                embedded_at=text("now()"),
                synced_at=text("now()"),
            ),
        )
    )


def _build_unity_world_vector_stmt(job: dict[str, Any], vec: list[float], model_id: str) -> Any:
    # world_semantic_plugin owns this store; core_graph → plugins is an accepted
    # layering inversion here (see CLAUDE.md world_semantic UnityWorldVector
    # move) since only this op needs it. Lazy import so a boot without the
    # plugin still loads the worker cleanly for the other job kinds.
    from plugins.world_semantic_plugin.stores.unity_world_vectors_store import build_upsert_stmt

    payload = job["payload"] or {}
    return build_upsert_stmt(
        world_id=str(payload.get("world_id") or ""),
        doc_kind=str(payload.get("doc_kind") or "object"),
        doc_id=str(payload.get("doc_id") or ""),
        content_text=str(payload.get("content_text") or ""),
        content_hash=str(job.get("content_hash") or ""),
        embedding=vec,
        model_id=model_id,
        meta=payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
    )


_STMT_BUILDERS = {
    "upsert_unity_world_vector": _build_unity_world_vector_stmt,
    "route": _build_route_embedding_stmt,
}

# Explicit producer op ids dispatched in process_batch. Any other "upsert_*"
# op_id is a producer this worker doesn't know how to build a statement for
# (e.g. an op removed along with its plugin) — it must be marked failed, not
# silently routed into the route_embeddings search index below.
_KNOWN_OPS = frozenset(_STMT_BUILDERS.keys()) - frozenset({"route"})


async def process_batch(jobs: list[dict[str, Any]]) -> None:
    """Embed an entire batch, grouping jobs by model selection to perform batch queries."""
    if not jobs:
        return

    from core.llm_config_service import resolve_route_embedding
    from db_layer.embeddings.embeddings_core import model_id_for, embed_documents_with
    from db_layer.embeddings.route_document import build_route_document

    # 1. Resolve model selection and model ID for each job
    resolved_jobs = []
    unknown_op_job_ids: list[int] = []
    for j in jobs:
        payload = j["payload"] or {}
        if not isinstance(payload, dict):
            payload = {}
        op_id = j["operation_id"]

        # An "upsert_*" op_id names an embedding producer. If it isn't one of
        # the ones this worker knows how to build (e.g. a producer whose
        # plugin was removed), it must not fall through to the route-embedding
        # default below — that would silently mis-classify it and poison the
        # route_embeddings search index instead of failing the job.
        if op_id.startswith("upsert_") and op_id not in _KNOWN_OPS:
            logger.warning(
                "embedding_worker: unknown producer op_id '%s' for job %s — marking failed",
                op_id, j["id"],
            )
            unknown_op_job_ids.append(j["id"])
            continue

        # Explicit op dispatch first — never fall through to route mis-classification.
        if op_id == "upsert_unity_world_vector":
            # world_semantic_plugin-owned; lazy so a boot without that plugin
            # still processes other job kinds (route).
            from plugins.world_semantic_plugin.stores.unity_world_vectors_store import (
                resolve_unity_world_embedding,
            )
            sel = await resolve_unity_world_embedding()
            builder = _build_unity_world_vector_stmt
            title = f"uwv:{payload.get('doc_kind', 'doc')}"
            txt = payload.get("content_text") or ""
        else:
            # Default: route embedding jobs (description payloads from job_producer)
            sel = await resolve_route_embedding()
            builder = _build_route_embedding_stmt
            title = j["operation_id"]
            txt = build_route_document(
                plugin_id=j["plugin_id"],
                operation_id=j["operation_id"],
                method=payload.get("method", ""),
                path=payload.get("path_template", ""),
                description=payload.get("description", ""),
                tags=payload.get("tags", []),
                workspace_label=payload.get("workspace_label"),
                param_names=payload.get("param_names", []),
            )
            j["doc_text"] = txt

        m_id = model_id_for(sel)

        resolved_jobs.append({
            "job": j,
            "text": str(txt),
            "title": title,
            "sel": sel,
            "model_id": m_id,
            "builder": builder
        })

    if unknown_op_job_ids:
        await _mark_failed(unknown_op_job_ids, "Unknown embedding producer op_id — no statement builder registered")

    # 2. Group by model_id
    from collections import defaultdict
    groups = defaultdict(list)
    for rj in resolved_jobs:
        groups[rj["model_id"]].append(rj)

    logger.info(
        "embedding_worker: processing batch of %d across %d model groups",
        len(jobs), len(groups),
    )

    failed_job_ids = []
    completed_jobs = []
    completed_vectors = []
    completed_model_ids = []
    completed_builders = []

    # 3. Embed for each group
    for m_id, rj_list in groups.items():
        sel = rj_list[0]["sel"]
        texts = [rj["text"] for rj in rj_list]
        titles = [rj["title"] for rj in rj_list]
        try:
            vectors = await embed_documents_with(sel, texts, titles=titles)
            if len(vectors) != len(rj_list):
                raise ValueError(f"embedder returned {len(vectors)} vectors for {len(rj_list)} texts")
            for rj, vec in zip(rj_list, vectors):
                completed_jobs.append(rj["job"])
                completed_vectors.append(vec)
                completed_model_ids.append(rj["model_id"])
                completed_builders.append(rj["builder"])
        except Exception as exc:
            logger.exception("embedding_worker: embed failed for model %s", m_id)
            failed_job_ids.extend([rj["job"]["id"] for rj in rj_list])

    # Mark failed groups
    if failed_job_ids:
        await _mark_failed(failed_job_ids, "Embedding API call failed for model group")

    # Upsert successful ones
    if completed_jobs:
        try:
            async with get_async_session() as session:
                for job, vec, model_id, builder in zip(completed_jobs, completed_vectors, completed_model_ids, completed_builders):
                    stmt = builder(job, vec, model_id)
                    await session.execute(stmt)
                stmt = (
                    update(EmbeddingJob)
                    .where(EmbeddingJob.id.in_([j["id"] for j in completed_jobs]))
                    .values(status="done", completed_at=func.now())
                )
                await session.execute(stmt)
                await session.commit()
            logger.info("embedding_worker: completed %d jobs (status=done)", len(completed_jobs))
        except Exception as exc:
            logger.exception("embedding_worker: upsert failed for batch")
            await _mark_failed([j["id"] for j in completed_jobs], str(exc))


async def _drain_until_idle(batch_size: int) -> None:
    """Claim and process batches back-to-back until the queue is empty."""
    while True:
        batch = await _claim_batch(batch_size)
        if not batch:
            return
        await process_batch(batch)


def _on_tick_error(exc: BaseException) -> None:
    """Classify drain failures: transient DB states log a warning, the rest a traceback."""
    exc_str = str(exc)
    # Gracefully handle transient database migration/reset schema and connection states
    if "relation" in exc_str and "does not exist" in exc_str:
        logger.warning(
            "embedding_worker: database relation does not exist yet (transient or pending migration)"
        )
    elif any(term in exc_str.lower() for term in ("connection", "refused", "shutting down", "starting up")):
        logger.warning(
            "embedding_worker: database connection temporarily unavailable: %s", exc
        )
    else:
        logger.exception("embedding_worker: drain failed: %s", exc)


async def _tick(batch_size: int) -> bool:
    """Drain the queue, then idle — the drain already loops until empty."""
    await _drain_until_idle(batch_size)
    return False


_WORKER = BackgroundWorker(
    "embedding_worker",
    _tick,
    interval=_IDLE_POLL_SECONDS,
    logger=logger,
    stop_timeout=_STOP_TIMEOUT,
    on_error=_on_tick_error,
)


def run(batch_size: int = _BATCH_SIZE,
        idle_poll: float = _IDLE_POLL_SECONDS) -> asyncio.Task:
    """Start the worker as a background task. Returns the task handle.

    Idempotent — calling ``run()`` again while a worker is already active
    returns the existing task.
    """
    return _WORKER.start(interval=idle_poll, batch_size=batch_size)


async def stop() -> None:
    """Signal the worker to exit on the next idle tick; await its completion."""
    await _WORKER.stop()


def register(registry) -> None:
    """
    Registers the embedding worker.
    """
    from core_graph.worker.worker_registry import WorkerSpec
    registry.register(WorkerSpec(name="embedding_worker", run=run, stop=stop, enabled_check=None))

