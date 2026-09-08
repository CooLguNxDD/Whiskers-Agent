"""Search engine vector store — ``search_content_vectors`` table.

Used by search_plugin semantic_index/semantic_search and portfolio STAR indexing.
Tenant-scoped; never default tenant_id.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from db_layer.connection import get_async_session
from db_layer.embeddings.search_engine import SearchSpec
from db_layer.embeddings.search_engine import search as _engine_search
from db_layer.models import SearchContentVector


def _row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "collection": row.collection,
        "content_text": row.content_text,
        "metadata": row.meta,
    }


_SEARCH_SEARCH_SPEC = SearchSpec(
    name="search",
    model=SearchContentVector,
    select_cols=(
        SearchContentVector.id,
        SearchContentVector.collection,
        SearchContentVector.content_text,
        SearchContentVector.meta,
    ),
    embedding_col=SearchContentVector.embedding,
    model_col=SearchContentVector.model,
    search_doc_col=getattr(SearchContentVector, "search_doc", None),
    key_fn=lambda c: c["id"],
    row_to_dict=_row_to_dict,
)


async def add_search_content_vector(
    collection: str,
    content_text: str,
    metadata: dict | None = None,
    *,
    tenant_id: int,
) -> dict[str, Any]:
    """Embed and store content in the search index."""
    from core.llm_config_service import resolve_tool_embedding
    from db_layer.embeddings.embeddings_core import embed_query_with, model_id_for

    content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
    sel = await resolve_tool_embedding("plugins.search_plugin", "semantic_index")
    model_id = model_id_for(sel)
    vector = await embed_query_with(sel, content_text)

    meta = metadata if metadata is not None else {}

    async with get_async_session() as session:
        row = SearchContentVector(
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


async def search_search_content_vectors(
    query: str,
    collection: str,
    top_k: int = 5,
    *,
    tenant_id: int,
) -> list[dict[str, Any]]:
    """Semantic (hybrid dense+sparse when enabled) similarity search over the search index."""
    from core.llm_config_service import resolve_tool_embedding

    sel = await resolve_tool_embedding("plugins.search_plugin", "semantic_index")

    def _extra_filters(stmt):
        return stmt.where(SearchContentVector.collection == collection).where(
            SearchContentVector.tenant_id == tenant_id
        )

    results = await _engine_search(
        _SEARCH_SEARCH_SPEC, query, sel, top_k, extra_filters=_extra_filters
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


_META_KEY_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


async def search_search_content_vectors_filtered(
    query: str,
    collection: str,
    top_k: int = 5,
    *,
    tenant_id: int,
    meta_equals: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Like ``search_search_content_vectors`` but with SQL-level ``meta[key] ==
    value`` predicates (JSONB ``->>``), for exact top_k on hard constraints
    (e.g. ``goal_class``/``passed``) instead of the Python-side post-fetch
    filtering ``core.memory.service.search_memory`` uses, which over-fetches
    ``top_k * 4`` and can still under-fill under a heavy filter.

    ``meta_equals`` keys are validated against ``^[a-z_][a-z0-9_]*$`` (they
    are interpolated into the bound param name and the JSONB path, never into
    a literal value) and mismatches raise ``ValueError`` rather than silently
    dropping the filter.
    """
    from core.llm_config_service import resolve_tool_embedding

    sel = await resolve_tool_embedding("plugins.search_plugin", "semantic_index")

    bad_keys = [k for k in (meta_equals or {}) if not _META_KEY_RE.match(k)]
    if bad_keys:
        raise ValueError(f"search_search_content_vectors_filtered: invalid meta key(s) {bad_keys!r}")

    def _extra_filters(stmt):
        stmt = stmt.where(SearchContentVector.collection == collection).where(
            SearchContentVector.tenant_id == tenant_id
        )
        for i, (key, value) in enumerate(sorted((meta_equals or {}).items())):
            stmt = stmt.where(
                text(f"CAST(meta AS JSONB) ->> :mk_{i} = :mv_{i}").bindparams(
                    **{f"mk_{i}": key, f"mv_{i}": str(value)}
                )
            )
        return stmt

    results = await _engine_search(
        _SEARCH_SEARCH_SPEC, query, sel, top_k, extra_filters=_extra_filters
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


async def list_search_content_vectors(
    collection: str,
    limit: int = 20,
    offset: int = 0,
    *,
    tenant_id: int,
) -> list[dict[str, Any]]:
    """List search index rows for a collection."""
    async with get_async_session() as session:
        stmt = (
            select(
                SearchContentVector.id,
                SearchContentVector.content_text,
                SearchContentVector.meta,
                SearchContentVector.created_at,
            )
            .where(SearchContentVector.collection == collection)
            .where(SearchContentVector.tenant_id == tenant_id)
            .order_by(
                SearchContentVector.created_at.desc(), SearchContentVector.id.desc()
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


async def delete_search_content_vector(
    row_id: int,
    collection: str | None = None,
    *,
    tenant_id: int,
) -> bool:
    """Delete a search index row by id."""
    async with get_async_session() as session:
        stmt = (
            delete(SearchContentVector)
            .where(SearchContentVector.id == row_id)
            .where(SearchContentVector.tenant_id == tenant_id)
        )
        if collection is not None:
            stmt = stmt.where(SearchContentVector.collection == collection)
        result = await session.execute(stmt)
        await session.commit()
        return (result.rowcount or 0) > 0


async def delete_search_content_vectors_by_meta(
    collection: str,
    key: str,
    value: str,
    *,
    tenant_id: int,
) -> int:
    """Delete every row in *collection* whose ``meta[key] == value`` (JSONB ->>).

    Used to replace stale RAG docs on re-index — the content_hash unique
    constraint dedupes identical text but leaves old rows (and their stale
    ``indexed_at``) in place when content actually changed. Callers delete
    prior rows for a ref/source before inserting the fresh one.
    """
    async with get_async_session() as session:
        result = await session.execute(
            text(
                "DELETE FROM search_content_vectors "
                "WHERE collection = :collection AND tenant_id = :tenant_id "
                "AND CAST(meta AS JSONB) ->> :key = :value"
            ),
            {"collection": collection, "tenant_id": tenant_id, "key": key, "value": value},
        )
        await session.commit()
        return int(result.rowcount or 0)


# Aliases matching the legacy content_vectors_store API (search backend).
add_content_vector = add_search_content_vector
search_content_vectors = search_search_content_vectors
list_content_vectors = list_search_content_vectors
delete_content_vector = delete_search_content_vector
