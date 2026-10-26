"""Durable asset desired state, enqueue and generation-guarded multimodal completion."""
from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, select, text, union_all, update
from sqlalchemy.dialects.postgresql import insert

from db_layer.connection import get_async_session
from db_layer.models import EmbeddingJob
from plugins.world_semantic_plugin.asset_models import WorldAssetEmbedding as Asset, WorldAssetVector as Vec
from plugins.world_semantic_plugin import asset_index as api
from db_layer.embeddings.multimodal import EmbeddingInput, EmbeddingMedia, validate_embedding_media

PLUGIN_ID = "world_semantic_plugin"
OPERATION_ID = "upsert_world_asset_embedding"
# Re-ingest may recover an interrupted asset claim; no automatic retry loop or generic job changes.
CLAIM_LEASE = timedelta(hours=1)
logger = logging.getLogger("whiskers.plugins.world_semantic")


def _identity(tenant_id, world_id, asset_id):
    return (Asset.tenant_id == tenant_id, Asset.world_id == world_id, Asset.asset_id == asset_id)


async def stage_assets(records: list[dict], *, tenant_id: int, world_id: str,
                       model_id: str, dimensions: int) -> dict:
    """Stage the entire batch and queue immutable snapshots in one transaction; unchanged is a no-op."""
    api.validate_namespace(tenant_id, world_id)
    enqueued = indexed = pending = 0
    async with get_async_session() as session:
        # Stable lock ordering prevents two overlapping batches from deadlocking.
        for record in sorted(records, key=lambda r: r["asset_id"]):
            asset_id = record["asset_id"]
            # Serialize absent-row insertion too; row locks alone cannot lock an absent identity.
            lock = f"asset:{tenant_id}:{world_id}:{asset_id}"
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock})
            existing = (await session.execute(select(Asset).where(*_identity(tenant_id, world_id, asset_id)).with_for_update())).scalar_one_or_none()
            if existing and existing.content_hash == record["content_hash"] and existing.status != "failed":
                if existing.status == "indexed":
                    indexed += 1
                    continue
                queued = (await session.execute(select(EmbeddingJob).where(
                    EmbeddingJob.plugin_id == PLUGIN_ID, EmbeddingJob.operation_id == OPERATION_ID,
                    EmbeddingJob.content_hash == record["content_hash"]))).scalar_one_or_none()
                # Core may have failed a job while its plugin was unloaded. Such desired state
                # remains enqueued, so inspect the queue before treating it as an active no-op.
                claimed_at = getattr(queued, "claimed_at", None)
                expired = (queued and queued.status == "processing" and claimed_at is not None
                           and claimed_at < datetime.now(timezone.utc) - CLAIM_LEASE)
                if queued and queued.status in ("pending", "processing") and not expired:
                    pending += 1
                    continue
            generation = uuid.uuid4().hex
            values = {"tenant_id": tenant_id, "world_id": world_id, "asset_id": asset_id,
                      "kind": record["meta"]["kind"], "content_text": record["content_text"],
                      "content_hash": record["content_hash"], "generation": generation,
                      "model": model_id, "dimensions": dimensions, "meta": record["meta"],
                      "embedding": None, "status": "enqueued", "embedded_at": None,
                      "updated_at": func.now()}
            stmt = insert(Asset).values(**values)
            await session.execute(stmt.on_conflict_do_update(
                index_elements=["tenant_id", "world_id", "asset_id"],
                set_={key: value for key, value in values.items() if key not in ("tenant_id", "world_id", "asset_id")}))
            payload = {"tenant_id": tenant_id, "world_id": world_id, "asset_id": asset_id,
                       "generation": generation, "model": model_id, "dimensions": dimensions,
                       "content_text": record["content_text"], "media": record["media"]}
            stmt = insert(EmbeddingJob).values(plugin_id=PLUGIN_ID, operation_id=OPERATION_ID,
                                               content_hash=record["content_hash"], payload=payload)
            await session.execute(stmt.on_conflict_do_update(
                constraint="embedding_jobs_dedup",
                set_={"payload": payload, "status": "pending", "last_error": None,
                      "attempts": 0, "claimed_at": None, "completed_at": None}))
            enqueued += 1
        if enqueued:
            await session.execute(text("NOTIFY embedding_jobs_new"))
        await session.commit()
    return {"status": "enqueued" if enqueued or pending else "indexed",
            "enqueued": enqueued, "pending": pending, "indexed": indexed}


def _documents(payload):
    parts = payload.get("media")
    if not isinstance(parts, list) or len(parts) > 2:
        raise ValueError("invalid asset job media")
    documents, total = [], 0
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("data"), str) or len(part["data"]) > (api.MAX_MEDIA_BYTES + 2) // 3 * 4:
            raise ValueError("invalid asset job media payload")
        medium = EmbeddingMedia(base64.b64decode(part["data"], validate=True), part.get("mime_type"))
        validate_embedding_media(medium, api.MAX_MEDIA_BYTES)
        total += len(medium.data)
        if total > 2 * api.MAX_MEDIA_BYTES:
            raise ValueError("oversized asset job media")
        documents.append(EmbeddingInput(text=payload["content_text"], media=medium))
    return documents or [EmbeddingInput(text=payload["content_text"])]


async def _finish(job: dict, vector: list[float] | None, *, failed: bool = False) -> None:
    payload = job["payload"]
    async with get_async_session() as session:
        # Match the producer's identity lock order before touching either row.
        lock = f"asset:{payload['tenant_id']}:{payload['world_id']}:{payload['asset_id']}"
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock})
        # Lock/check the live queue row: an A->B->A requeue may have reused this job id/hash.
        current = (await session.execute(select(EmbeddingJob).where(EmbeddingJob.id == job["id"]).with_for_update())).scalar_one_or_none()
        if (current is None or current.plugin_id != PLUGIN_ID or current.operation_id != OPERATION_ID
                or current.payload.get("generation") != payload["generation"]
                or current.status != "processing"):
            return
        stmt = update(Asset).where(
            *_identity(payload["tenant_id"], payload["world_id"], payload["asset_id"]),
            Asset.generation == payload["generation"], Asset.content_hash == job["content_hash"],
            Asset.model == payload["model"], Asset.dimensions == payload["dimensions"])
        values = {"status": "failed" if failed else "indexed", "embedding": vector,
                  "embedded_at": None if failed else func.now(), "updated_at": func.now()}
        await session.execute(stmt.values(**values))
        await session.execute(update(EmbeddingJob).where(EmbeddingJob.id == job["id"]).values(
            status="failed" if failed else "done", completed_at=None if failed else func.now(),
            last_error="Asset embedding or persistence failed; re-ingest to retry" if failed else None))
        await session.commit()


async def consume_asset_job(job: dict) -> None:
    """Existing worker consumer: S01 embeddings, atomic publication/status, explicit retry on re-ingest."""
    payload = job["payload"]
    if payload.get("schema") == "v3":
        from plugins.world_semantic_plugin import asset_v3
        return await asset_v3.consume_v3_job(job)
    try:
        api.validate_namespace(payload["tenant_id"], payload["world_id"])
        sel = await api.resolve_asset_embedding()
        if api.embeddings_core.model_id_for(sel) != payload["model"] or api.dimensions_for(sel) != payload["dimensions"]:
            raise ValueError("asset embedding selection changed; re-ingest for current model")
        documents = await asyncio.to_thread(_documents, payload)
        vectors = await api.embeddings_core.embed_multimodal_with(sel, documents)
        if len(vectors) != len(documents):
            raise ValueError("asset embedding cardinality mismatch")
        vector = api.aggregate_vectors(vectors, payload["dimensions"])
        await _finish(job, vector)
    except Exception:
        # No provider exception text, bytes, paths or credentials in durable errors/logs.
        logger.warning("asset embedding job failed id=%s", job.get("id"))
        await _finish(job, None, failed=True)


def register_consumer() -> None:
    """Attach the asset-specific identity to the existing registered embedding worker."""
    from core_graph.worker.embedding_worker import register_embedding_consumer
    register_embedding_consumer(PLUGIN_ID, OPERATION_ID, consume_asset_job)


def unregister_consumer() -> None:
    """Detach only the asset consumer on plugin unload; leave unity/route jobs unchanged."""
    from core_graph.worker.embedding_worker import unregister_embedding_consumer
    unregister_embedding_consumer(PLUGIN_ID, OPERATION_ID)


# ---------------------------------------------------------------- WorldGen v3 pack library

async def stage_v3_assets(records: list[dict], *, tenant_id: int, world_id: str,
                          model_id: str, dimensions: int) -> dict:
    """Stage v3 pack records + jobs in one transaction; unchanged hash+model is a no-op."""
    from plugins.world_semantic_plugin.asset_v3 import SCHEMA, decide_stage
    api.validate_namespace(tenant_id, world_id)
    enqueued = indexed = pending = 0
    async with get_async_session() as session:
        for record in sorted(records, key=lambda r: r["asset_id"]):
            asset_id, meta = record["asset_id"], record["meta"]
            lock = f"asset:{tenant_id}:{world_id}:{asset_id}"
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock})
            existing = (await session.execute(select(Asset).where(*_identity(tenant_id, world_id, asset_id)).with_for_update())).scalar_one_or_none()
            queue_active = False
            if existing and existing.content_hash == record["content_hash"] and existing.status == "enqueued":
                queued = (await session.execute(select(EmbeddingJob).where(
                    EmbeddingJob.plugin_id == PLUGIN_ID, EmbeddingJob.operation_id == OPERATION_ID,
                    EmbeddingJob.content_hash == record["content_hash"]))).scalar_one_or_none()
                claimed_at = getattr(queued, "claimed_at", None)
                expired = (queued and queued.status == "processing" and claimed_at is not None
                           and claimed_at < datetime.now(timezone.utc) - CLAIM_LEASE)
                queue_active = bool(queued and queued.status in ("pending", "processing") and not expired)
            verdict = decide_stage(getattr(existing, "content_hash", None), getattr(existing, "status", None),
                                   record["content_hash"], queue_active)
            if verdict == "indexed":
                indexed += 1
                continue
            if verdict == "pending":
                pending += 1
                continue
            generation = uuid.uuid4().hex
            values = {"tenant_id": tenant_id, "world_id": world_id, "asset_id": asset_id,
                      "kind": meta["kind"], "content_text": record["caption"] or asset_id,
                      "content_hash": record["content_hash"], "generation": generation,
                      "model": model_id, "dimensions": dimensions, "meta": meta, "embedding": None,
                      "status": "enqueued", "embedded_at": None, "updated_at": func.now(),
                      "pack_id": meta["pack_id"], "distribution": meta["distribution"],
                      "tri_count": meta["tri_count"], "role": meta["role"], "biome": meta["biome"],
                      "licence": meta["licence"]}
            await session.execute(insert(Asset).values(**values).on_conflict_do_update(
                index_elements=["tenant_id", "world_id", "asset_id"],
                set_={k: v for k, v in values.items() if k not in ("tenant_id", "world_id", "asset_id")}))
            # Changed content clears only this asset's previous vectors.
            await session.execute(delete(Vec).where(Vec.tenant_id == tenant_id, Vec.world_id == world_id,
                                                    Vec.asset_id == asset_id))
            payload = {"schema": SCHEMA, "tenant_id": tenant_id, "world_id": world_id, "asset_id": asset_id,
                       "generation": generation, "model": model_id, "dimensions": dimensions,
                       "caption": record["caption"], "views": record["views"]}
            await session.execute(insert(EmbeddingJob).values(
                plugin_id=PLUGIN_ID, operation_id=OPERATION_ID, content_hash=record["content_hash"],
                payload=payload).on_conflict_do_update(
                constraint="embedding_jobs_dedup",
                set_={"payload": payload, "status": "pending", "last_error": None,
                      "attempts": 0, "claimed_at": None, "completed_at": None}))
            enqueued += 1
        if enqueued:
            await session.execute(text("NOTIFY embedding_jobs_new"))
        await session.commit()
    return {"status": "enqueued" if enqueued or pending else "indexed",
            "enqueued": enqueued, "pending": pending, "indexed": indexed}


async def finish_v3_job(job: dict, rows: list[dict] | None, *, failed: bool = False) -> None:
    """Publish all vectors + status atomically when generation/hash/model still match; else drop."""
    payload = job["payload"]
    async with get_async_session() as session:
        lock = f"asset:{payload['tenant_id']}:{payload['world_id']}:{payload['asset_id']}"
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock})
        current = (await session.execute(select(EmbeddingJob).where(EmbeddingJob.id == job["id"]).with_for_update())).scalar_one_or_none()
        if (current is None or current.plugin_id != PLUGIN_ID or current.operation_id != OPERATION_ID
                or current.payload.get("generation") != payload["generation"]
                or current.status != "processing"):
            return
        stmt = update(Asset).where(
            *_identity(payload["tenant_id"], payload["world_id"], payload["asset_id"]),
            Asset.generation == payload["generation"], Asset.content_hash == job["content_hash"],
            Asset.model == payload["model"], Asset.dimensions == payload["dimensions"])
        result = await session.execute(stmt.values(
            status="failed" if failed else "indexed", embedded_at=None if failed else func.now(),
            updated_at=func.now()))
        if not failed and result.rowcount:
            for row in rows or []:
                await session.execute(insert(Vec).values(
                    tenant_id=payload["tenant_id"], world_id=payload["world_id"], asset_id=payload["asset_id"],
                    vector_kind=row["vector_kind"], view_idx=row["view_idx"], model=payload["model"],
                    dimensions=payload["dimensions"], generation=payload["generation"],
                    embedding=row["embedding"]))
        await session.execute(update(EmbeddingJob).where(EmbeddingJob.id == job["id"]).values(
            status="failed" if failed else "done", completed_at=None if failed else func.now(),
            last_error="Asset embedding or persistence failed; re-ingest to retry" if failed else None))
        await session.commit()


def build_search_statement(vector: list[float], *, tenant_id: int, world_id: str, model_id: str,
                           k: int, filters: dict, kind: str | None = None, tags: list[str] | None = None):
    """Max-sim over views (caption included; v1 single vectors count as one view), filters in SQL."""
    # Candidate vectors of the requested identity only: v3 view/caption rows plus v1 row vectors.
    vecs = union_all(
        select(Vec.tenant_id.label("tenant_id"), Vec.world_id.label("world_id"),
               Vec.asset_id.label("asset_id"), Vec.embedding.label("embedding"))
        .where(Vec.tenant_id == tenant_id, Vec.world_id == world_id, Vec.model == model_id),
        select(Asset.tenant_id, Asset.world_id, Asset.asset_id, Asset.embedding)
        .where(Asset.tenant_id == tenant_id, Asset.world_id == world_id, Asset.model == model_id,
               Asset.embedding.isnot(None))).subquery("vecs")
    sim = 1 - vecs.c.embedding.cosine_distance(vector)
    score, mean = func.max(sim).label("score"), func.avg(sim).label("mean_score")
    stmt = (select(Asset.asset_id, Asset.meta, score, mean)
            .select_from(vecs.join(Asset, and_(Asset.tenant_id == vecs.c.tenant_id,
                                               Asset.world_id == vecs.c.world_id,
                                               Asset.asset_id == vecs.c.asset_id)))
            .where(Asset.tenant_id == tenant_id, Asset.world_id == world_id, Asset.status == "indexed",
                   Asset.model == model_id))
    if kind is not None:
        stmt = stmt.where(Asset.kind == kind)
    if tags:
        stmt = stmt.where(Asset.meta["tags"].contains(tags))
    for name, column in (("role", Asset.role), ("biome", Asset.biome), ("licence", Asset.licence),
                         ("pack", Asset.pack_id)):
        if name in filters:
            stmt = stmt.where(column == filters[name])
    if "max_tris" in filters:
        stmt = stmt.where(Asset.tri_count.isnot(None), Asset.tri_count <= filters["max_tris"])
    if filters.get("distribution", "shared") != "any":
        stmt = stmt.where(Asset.distribution == filters.get("distribution", "shared"))
    return (stmt.group_by(Asset.tenant_id, Asset.world_id, Asset.asset_id)
            .order_by(score.desc(), mean.desc(), Asset.asset_id).limit(k))


async def search_asset_vectors(vector: list[float], *, tenant_id: int, world_id: str, model_id: str,
                               k: int, filters: dict, kind: str | None = None,
                               tags: list[str] | None = None) -> list[dict]:
    """Run the tenant-scoped max-sim query; filters narrow rows before the LIMIT."""
    stmt = build_search_statement(vector, tenant_id=tenant_id, world_id=world_id, model_id=model_id,
                                  k=k, filters=filters, kind=kind, tags=tags)
    async with get_async_session() as session:
        rows = (await session.execute(stmt)).all()
    return [{"asset_id": r.asset_id, "metadata": r.meta, "score": float(r.score),
             "mean_score": float(r.mean_score)} for r in rows]
