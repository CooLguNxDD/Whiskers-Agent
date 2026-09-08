"""Unity world RAG vector store — isolated from content_vectors / routes.

Table: unity_world_vectors (plugin migration 0004).
Embedding writes are owned by embedding_worker (operation_id=upsert_unity_world_vector).
This module provides search / purge / identity helpers for producers and query paths.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from db_layer.connection import get_async_session
from db_layer.embeddings.search_engine import SearchSpec
from db_layer.embeddings.search_engine import search as _engine_search
from plugins.world_semantic_plugin.models import UnityWorldVector

logger = logging.getLogger("whiskers")

PLUGIN_ID = "world_semantic_plugin"
EMBED_TOOL = "unity_world_semantic"
OPERATION_ID = "upsert_unity_world_vector"
DOC_KIND_OBJECT = "object"
DOC_KIND_HEX = "hex"
DOC_KIND_HEX_LAYER = "hex_layer"


async def resolve_unity_world_embedding() -> dict:
    """Use the server global embedding config (EMBED_* / active pool).

    Same dimensions as route / content embeddings — never a separate dim.
    Optional tool override ``unity_world_semantic`` is only applied when it
    points at a pool entry; otherwise falls through to ``resolve_embedding()``.
    """
    from core.llm_config_service import resolve_embedding, resolve_tool_embedding

    try:
        sel = await resolve_tool_embedding(
            f"plugins.{PLUGIN_ID}",
            EMBED_TOOL,
        )
        # Tool resolver returns env defaults when no tool mapping exists —
        # always fine; dimensions come from EMBED_DIMENSIONS via _env_embedding.
        if sel:
            return sel
    except Exception as exc:
        logger.warning(
            "unity embedding: tool override %s failed (%s); falling back to global",
            EMBED_TOOL,
            exc,
        )
    return await resolve_embedding()


def identity_content_hash(
    world_id: str,
    doc_kind: str,
    doc_id: str,
    model_id: str,
    content_text: str,
) -> str:
    """Stable job + change-detect hash (includes identity so texts cannot collide)."""
    raw = f"{world_id}|{doc_kind}|{doc_id}|{model_id}|{content_text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def delete_world_vectors(world_id: str) -> int:
    """Purge all RAG vectors for a world (full reindex replace=true)."""
    async with get_async_session() as session:
        result = await session.execute(
            delete(UnityWorldVector).where(UnityWorldVector.world_id == world_id)
        )
        await session.commit()
        return int(result.rowcount or 0)


async def delete_doc(
    world_id: str,
    doc_kind: str,
    doc_id: str,
) -> int:
    """Remove one document's vectors (all models)."""
    async with get_async_session() as session:
        result = await session.execute(
            delete(UnityWorldVector).where(
                UnityWorldVector.world_id == world_id,
                UnityWorldVector.doc_kind == doc_kind,
                UnityWorldVector.doc_id == doc_id,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


async def search_unity_world_vectors(
    query: str,
    world_id: str,
    *,
    top_k: int = 10,
    doc_kind: str | None = None,
) -> list[dict[str, Any]]:
    """Semantic (hybrid dense+sparse when enabled) search over unity_world_vectors for one world."""
    if not query or not query.strip() or not world_id:
        return []

    sel = await resolve_unity_world_embedding()
    clamped_top_k = max(1, min(int(top_k), 100))

    def _extra_filters(stmt):
        stmt = stmt.where(UnityWorldVector.world_id == world_id)
        if doc_kind:
            stmt = stmt.where(UnityWorldVector.doc_kind == doc_kind)
        return stmt

    results = await _engine_search(
        _UNITY_SEARCH_SPEC, query.strip(), sel, clamped_top_k, extra_filters=_extra_filters
    )
    return [
        {
            "id": r["id"],
            "world_id": r["world_id"],
            "doc_kind": r["doc_kind"],
            "doc_id": r["doc_id"],
            "content_text": r["content_text"],
            "metadata": r["metadata"] or {},
            "similarity": float(r["score"] or 0.0),
        }
        for r in results
    ]


def _hex_id_from_hit(kind: str | None, hit: dict, meta: dict) -> str | None:
    """Map a unity_world_vectors hit onto a 2D hex id for encoder ranking."""
    if kind == DOC_KIND_HEX:
        hid = hit.get("doc_id")
        return str(hid) if hid else None
    if kind == DOC_KIND_OBJECT:
        hid = meta.get("hex_id")
        return str(hid) if hid else None
    if kind == DOC_KIND_HEX_LAYER:
        # Prefer structured meta; fall back to doc_id "{hexId}#L{n}".
        hid = meta.get("hex_id")
        if hid:
            return str(hid)
        doc_id = str(hit.get("doc_id") or "")
        if "#L" in doc_id:
            return doc_id.split("#L", 1)[0]
        return doc_id or None
    return None


async def hex_similarity_scores(
    query: str,
    world_id: str,
    hex_ids: list[str],
    *,
    top_k: int = 50,
) -> dict[str, float]:
    """Aggregate RAG similarity onto hex ids for encoder ranking.

    Object hits contribute via meta.hex_id; hex docs via doc_id;
    hex_layer docs via meta.hex_id (or doc_id prefix before ``#L``).
    Score per hex = max similarity among matching hits.
    """
    scores = {h: 0.0 for h in hex_ids}
    if not hex_ids or not query.strip():
        return scores

    wanted = set(hex_ids)
    hits = await search_unity_world_vectors(
        query,
        world_id,
        top_k=top_k,
        doc_kind=None,
    )
    for hit in hits:
        sim = float(hit.get("similarity") or 0.0)
        kind = hit.get("doc_kind")
        meta = hit.get("metadata") or {}
        hid = _hex_id_from_hit(kind, hit, meta if isinstance(meta, dict) else {})
        if hid and hid in wanted:
            scores[hid] = max(scores[hid], sim)
    return scores


def _expected_embed_dimensions() -> int:
    """Global EMBED_DIMENSIONS (same as .env / migration VECTOR width)."""
    try:
        dim = int(os.environ.get("EMBED_DIMENSIONS", "") or 1536)
    except (TypeError, ValueError):
        dim = 1536
    return dim if dim > 0 else 1536


def coerce_embedding(vec: list[float], *, expected: int | None = None) -> list[float]:
    """Force vector length to global EMBED_DIMENSIONS (default 1500).

    Local servers often ignore the OpenAI ``dimensions`` param (e.g. return 768
    while EMBED_DIMENSIONS=1500). We always store exactly EMBED_DIMENSIONS:
      - longer  → truncate (Matryoshka-style)
      - shorter → zero-pad
    """
    target = expected if expected is not None else _expected_embed_dimensions()
    if not vec:
        raise ValueError(f"empty embedding (expected {target} dims)")
    n = len(vec)
    if n == target:
        return list(vec)
    if n > target:
        logger.warning(
            "unity_world_vectors: truncating embedding %d → %d (EMBED_DIMENSIONS)",
            n,
            target,
        )
        return list(vec[:target])
    logger.warning(
        "unity_world_vectors: padding embedding %d → %d (EMBED_DIMENSIONS)",
        n,
        target,
    )
    out = list(vec)
    out.extend([0.0] * (target - n))
    return out


def _unity_row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "world_id": row.world_id,
        "doc_kind": row.doc_kind,
        "doc_id": row.doc_id,
        "content_text": row.content_text,
        "metadata": row.meta,
    }


# Defined after coerce_embedding (used as vector_transform) — search_unity_world_vectors
# looks this up at call time, so definition order only matters for this module-load line.
_UNITY_SEARCH_SPEC = SearchSpec(
    name="unity",
    model=UnityWorldVector,
    select_cols=(
        UnityWorldVector.id,
        UnityWorldVector.world_id,
        UnityWorldVector.doc_kind,
        UnityWorldVector.doc_id,
        UnityWorldVector.content_text,
        UnityWorldVector.meta,
    ),
    embedding_col=UnityWorldVector.embedding,
    model_col=UnityWorldVector.model,
    search_doc_col=getattr(UnityWorldVector, "search_doc", None),
    key_fn=lambda c: c["id"],
    row_to_dict=_unity_row_to_dict,
    vector_transform=coerce_embedding,
)


def build_upsert_stmt(
    *,
    world_id: str,
    doc_kind: str,
    doc_id: str,
    content_text: str,
    content_hash: str,
    embedding: list[float],
    model_id: str,
    meta: dict | None,
):
    """SQLAlchemy upsert used by embedding_worker."""
    from sqlalchemy import func

    embedding = coerce_embedding(embedding)

    return (
        insert(UnityWorldVector)
        .values(
            world_id=world_id,
            doc_kind=doc_kind,
            doc_id=doc_id,
            content_text=content_text,
            content_hash=content_hash,
            embedding=embedding,
            model=model_id,
            meta=meta or {},
            embedded_at=func.now(),
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            constraint="unity_world_vectors_identity_unique",
            set_=dict(
                content_text=content_text,
                content_hash=content_hash,
                embedding=embedding,
                meta=meta or {},
                embedded_at=func.now(),
                updated_at=func.now(),
            ),
        )
    )
