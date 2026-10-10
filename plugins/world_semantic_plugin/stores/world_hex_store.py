"""Tenant-scoped world-hex reservation, durable embedding jobs and revision-CAS writes.

Reuses unity_world_vectors without overwriting legacy object/hex/layer documents.
A pending revision invalidates its old vector; failure stays retryable, not indexed.
"""
from __future__ import annotations

import base64
import math
from dataclasses import asdict, replace

import h3
from sqlalchemy import BigInteger, cast, func, select, update
from sqlalchemy.dialects.postgresql import insert

from db_layer.connection import get_async_session
from db_layer.embeddings.embeddings_core import embed_multimodal_with, model_id_for
from db_layer.embeddings.multimodal import EmbeddingInput, EmbeddingMedia, _validate_media
from db_layer.embeddings.search_engine import search as _engine_search
from db_layer.models import EmbeddingJob
from plugins.world_semantic_plugin.hexmath import Projection
from plugins.world_semantic_plugin.models import UnityWorldVector
from plugins.world_semantic_plugin.stores.unity_world_vectors_store import (
    PLUGIN_ID, _UNITY_SEARCH_SPEC, resolve_unity_world_embedding,
)
from plugins.world_semantic_plugin.world_documents import (
    AuthorizedWorld, MAX_IMAGE_BYTES, MAX_METADATA_BYTES, MAX_REVISION,
    WorldDocument, document_hash,
)

DOC_KIND = "world_hex"
OPERATION_ID = "upsert_world_hex_multimodal"
MAX_K = 50
# Dense semantic search only: pending/unembedded summaries cannot be sparse matches.
_SEARCH_SPEC = replace(_UNITY_SEARCH_SPEC, name="world_hex", search_doc_col=None, vector_transform=None)


def validate_selection(selection: dict) -> tuple[str, int]:
    """Require an explicitly configured multimodal space; never use a text fallback."""
    from core.llm_provider_management import get_llm_provider_registry
    from utils.embedding_config import configured_dimensions

    provider = selection.get("provider")
    spec = get_llm_provider_registry().get(provider) if isinstance(provider, str) else None
    if not spec or spec.multimodal_embeddings_factory is None:
        raise ValueError("world hex indexing/search requires a multimodal embedding selection")
    model = selection.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("configured multimodal model is required")
    dimensions = configured_dimensions(selection.get("dimensions"), strict=True)
    return model_id_for(selection | {"dimensions": dimensions}), dimensions


async def reserve_document(doc: WorldDocument) -> str:
    """Serialize producers per identity and atomically reserve + enqueue a target revision."""
    namespace = doc.context.namespace
    identity = dict(world_id=namespace, doc_kind=DOC_KIND, doc_id=doc.hex_id, model=doc.model_id)
    async with get_async_session() as session:
        await session.execute(insert(UnityWorldVector).values(
            **identity, content_text="", content_hash="", meta={}, embedding=None,
        ).on_conflict_do_nothing(constraint="unity_world_vectors_identity_unique"))
        row = (await session.execute(select(UnityWorldVector).where(
            UnityWorldVector.world_id == namespace,
            UnityWorldVector.doc_kind == DOC_KIND,
            UnityWorldVector.doc_id == doc.hex_id,
            UnityWorldVector.model == doc.model_id,
        ).with_for_update())).scalar_one()
        previous = int((row.meta or {}).get("revision", 0))
        if doc.revision < previous:
            await session.commit()
            return "stale"
        same = row.content_hash == doc.content_hash
        if doc.revision == previous and not same:
            raise ValueError("same revision cannot describe different content")
        row.meta = {
            "tenant_id": doc.context.tenant_id, "world_id": doc.context.world_id,
            "hex_id": doc.hex_id, "revision": doc.revision,
            "projection": asdict(doc.context.projection),
            "center_pos": doc.center_pos, "center_frame": "unity_xz_meters",
        }
        row.updated_at = func.now()
        if same and row.embedding is not None:
            await session.commit()
            return "unchanged"
        row.content_text = doc.summary
        row.content_hash = doc.content_hash
        row.embedding = None
        row.embedded_at = None
        # Includes revision to avoid ABA hashes reusing an older processing/done job.
        import hashlib
        queue_hash = hashlib.sha256(f"{doc.content_hash}:{doc.revision}".encode()).hexdigest()
        stmt = insert(EmbeddingJob).values(
            plugin_id=PLUGIN_ID, operation_id=OPERATION_ID,
            content_hash=queue_hash, payload=doc.job_payload(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["plugin_id", "operation_id", "content_hash"],
            set_={"status": "pending", "last_error": None, "attempts": 0,
                  "claimed_at": None, "completed_at": None},
            where=EmbeddingJob.status == "failed",
        )
        await session.execute(stmt)
        # The registered embedding_worker polls the same durable queue; no extra loop.
        await session.commit()
        return "queued"


def _input_from_job(payload: dict) -> tuple[AuthorizedWorld, EmbeddingInput]:
    context = AuthorizedWorld(payload["tenant_id"], payload["world_id"], Projection(**payload["projection"]))
    if context.namespace != payload.get("namespace"):
        raise ValueError("invalid queued world namespace")
    hex_id, revision = payload["hex_id"], payload["revision"]
    if (not h3.is_valid_cell(hex_id) or h3.get_resolution(hex_id) != context.projection.base_res
            or type(revision) is not int or not 1 <= revision <= MAX_REVISION):
        raise ValueError("invalid queued hex/revision")
    summary = payload["summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary.encode()) > MAX_METADATA_BYTES + 10000:
        raise ValueError("invalid queued summary")
    encoded = payload.get("image_base64")
    media = None
    if encoded is not None:
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError("invalid queued image")
        media = EmbeddingMedia(base64.b64decode(encoded, validate=True), payload["mime_type"])
        if media.mime_type not in ("image/png", "image/jpeg"):
            raise ValueError("queued snapshot must be PNG/JPEG")
        _validate_media(media, MAX_IMAGE_BYTES)
    if document_hash(context.namespace, hex_id, payload["model_id"], summary, media) != payload["content_hash"]:
        raise ValueError("queued document hash mismatch")
    return context, EmbeddingInput(text=summary, media=media)


def build_completion_stmt(payload: dict, vector: list[float], dimensions: int):
    """Write only the currently reserved revision/hash; a stale job updates zero rows."""
    if (not isinstance(vector, list) or len(vector) != dimensions
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
        raise ValueError("world hex embedding must have exact finite configured dimensions")
    return update(UnityWorldVector).where(
        UnityWorldVector.world_id == payload["namespace"],
        UnityWorldVector.doc_kind == DOC_KIND,
        UnityWorldVector.doc_id == payload["hex_id"],
        UnityWorldVector.model == payload["model_id"],
        UnityWorldVector.content_hash == payload["content_hash"],
        cast(UnityWorldVector.meta["revision"].astext, BigInteger) == payload["revision"],
    ).values(embedding=vector, embedded_at=func.now(), updated_at=func.now())


async def process_world_hex_jobs(jobs: list[dict], mark_failed) -> None:
    """Registered shared consumer delegates this operation; failures remain failed/retryable.

    Process serially to bound media/provider memory independently of the shared claim
    batch size. Success writes and job completion commit in the same transaction.
    """
    import asyncio
    for job in jobs:
        try:
            if job["plugin_id"] != PLUGIN_ID or job["operation_id"] != OPERATION_ID:
                raise ValueError("world hex consumer identity mismatch")
            payload = job["payload"]
            _, document = await asyncio.to_thread(_input_from_job, payload)
            selection = await resolve_unity_world_embedding()
            identity, dimensions = validate_selection(selection)
            if identity != payload["model_id"]:
                raise ValueError("queued model selection changed; reingest in the new vector space")
            vectors = await embed_multimodal_with(selection, [document])
            if len(vectors) != 1:
                raise ValueError("world hex vector cardinality mismatch")
            completion = build_completion_stmt(payload, vectors[0], dimensions)
            async with get_async_session() as session:
                await session.execute(completion)
                # Stale CAS is a successful no-op, never replaces a newer revision.
                await session.execute(update(EmbeddingJob).where(
                    EmbeddingJob.id == job["id"],
                    EmbeddingJob.plugin_id == PLUGIN_ID,
                    EmbeddingJob.operation_id == OPERATION_ID,
                    EmbeddingJob.content_hash == job["content_hash"],
                ).values(status="done", completed_at=func.now(), last_error=None))
                await session.commit()
        except Exception:
            # Sanitized error only: provider/media/transport exception bodies may be sensitive.
            await mark_failed([job["id"]], "World hex validation, model selection, embedding or persistence failed")


async def search_documents(context: AuthorizedWorld, query: str, selection: dict, k: int) -> list[dict]:
    """Filter authorized namespace + kind + configured space before dense top-k."""
    _, dimensions = validate_selection(selection)
    def filters(stmt):
        return stmt.where(
            UnityWorldVector.world_id == context.namespace,
            UnityWorldVector.doc_kind == DOC_KIND,
            # Defense in depth: the namespace already encodes the tenant; rows also carry it.
            UnityWorldVector.meta["tenant_id"].astext == str(context.tenant_id),
            func.vector_dims(UnityWorldVector.embedding) == dimensions,
        )
    # Canonicalize numeric configuration strings just as the S01 client does,
    # so SQL model identity cannot drift (e.g. configured "03" vs stored :3).
    selection = selection | {"dimensions": dimensions}
    return await _engine_search(_SEARCH_SPEC, query, selection, k, extra_filters=filters)
