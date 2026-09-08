"""Core memory / harness RAG store — ``memory_content_vectors`` table.

Used by core.memory service (agent notes, plan recipes, anti-patterns).
Tenant-scoped; never default tenant_id.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from db_layer.connection import get_async_session
from db_layer.embeddings.search_engine import SearchSpec
from db_layer.embeddings.search_engine import search as _engine_search
from db_layer.models import MemoryContentVector


def _row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "collection": row.collection,
        "content_text": row.content_text,
        "metadata": row.meta,
    }


_MEMORY_SEARCH_SPEC = SearchSpec(
    name="memory",
    model=MemoryContentVector,
    select_cols=(
        MemoryContentVector.id,
        MemoryContentVector.collection,
        MemoryContentVector.content_text,
        MemoryContentVector.meta,
    ),
    embedding_col=MemoryContentVector.embedding,
    model_col=MemoryContentVector.model,
    search_doc_col=getattr(MemoryContentVector, "search_doc", None),
    key_fn=lambda c: c["id"],
    row_to_dict=_row_to_dict,
)


async def add_memory_content_vector(
    collection: str,
    content_text: str,
    metadata: dict | None = None,
    *,
    tenant_id: int,
) -> dict[str, Any]:
    """Embed and store a memory / harness vector row."""
    from core.llm_config_service import resolve_tool_embedding
    from db_layer.embeddings.embeddings_core import embed_query_with, model_id_for

    content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
    # Reuse search_plugin embedding resolution (shared embedding pool).
    sel = await resolve_tool_embedding("plugins.search_plugin", "semantic_index")
    model_id = model_id_for(sel)
    vector = await embed_query_with(sel, content_text)

    meta = metadata if metadata is not None else {}

    async with get_async_session() as session:
        row = MemoryContentVector(
            collection=collection,
            content_text=content_text,
            embedding=vector,
            model=model_id,
            meta=meta,
            content_hash=content_hash,
            tenant_id=tenant_id,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {"status": "ok", "deduped": True}

        return {"status": "ok", "id": row.id}


async def search_memory_content_vectors(
    query: str,
    collection: str,
    top_k: int = 5,
    *,
    tenant_id: int,
) -> list[dict[str, Any]]:
    """Semantic (hybrid dense+sparse when enabled) similarity search over memory vectors."""
    from core.llm_config_service import resolve_tool_embedding

    sel = await resolve_tool_embedding("plugins.search_plugin", "semantic_index")

    def _extra_filters(stmt):
        return stmt.where(MemoryContentVector.collection == collection).where(
            MemoryContentVector.tenant_id == tenant_id
        )

    results = await _engine_search(
        _MEMORY_SEARCH_SPEC, query, sel, top_k, extra_filters=_extra_filters
    )
    return [
        {
            "id": r["id"],
            "collection": r["collection"],
            "content_text": r["content_text"],
            "metadata": r["metadata"],
            "similarity": r["score"],
        }
        for r in results
    ]


async def list_memory_content_vectors(
    collection: str,
    limit: int = 20,
    offset: int = 0,
    *,
    tenant_id: int,
) -> list[dict[str, Any]]:
    """List memory rows for a collection."""
    async with get_async_session() as session:
        stmt = (
            select(
                MemoryContentVector.id,
                MemoryContentVector.content_text,
                MemoryContentVector.meta,
                MemoryContentVector.created_at,
            )
            .where(MemoryContentVector.collection == collection)
            .where(MemoryContentVector.tenant_id == tenant_id)
            .order_by(
                MemoryContentVector.created_at.desc(), MemoryContentVector.id.desc()
            )
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(stmt)
        return [
            {
                "id": row.id,
                "content_text": row.content_text,
                "metadata": row.meta,
                "created_at": row.created_at.isoformat()
                if row.created_at is not None
                else None,
            }
            for row in result.all()
        ]


async def delete_memory_content_vector(
    row_id: int,
    collection: str | None = None,
    *,
    tenant_id: int,
) -> bool:
    """Delete a memory row by id."""
    async with get_async_session() as session:
        stmt = (
            delete(MemoryContentVector)
            .where(MemoryContentVector.id == row_id)
            .where(MemoryContentVector.tenant_id == tenant_id)
        )
        if collection is not None:
            stmt = stmt.where(MemoryContentVector.collection == collection)
        result = await session.execute(stmt)
        await session.commit()
        return (result.rowcount or 0) > 0
