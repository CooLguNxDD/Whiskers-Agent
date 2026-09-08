"""Unified hybrid/dense embedding search engine.

Single choke point every embedding search (routes, memory, search index,
messages, unity world) funnels through, so hybrid-vs-dense is
decided in exactly one place with consistent scoring — instead of hybrid
existing only for routes while every other table hand-rolled its own
dense-only query/order/map boilerplate.

``rrf_fuse``/``normalize_rrf_scores`` were moved here (generalized with a
caller-supplied key function) from ``embeddings_routes.py``, which used to
hardcode the fusion key to ``(plugin_id, operation_id)``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Hashable, Sequence

from sqlalchemy import func, select
from sqlalchemy import text as sa_text

from db_layer.connection import get_async_session
from db_layer.embeddings.embeddings_core import embed_query_with, model_id_for
from utils.server_config import (
    HYBRID_DENSE_TOP_N,
    HYBRID_FUSED_TOP_N,
    HYBRID_RRF_K,
    HYBRID_SEARCH_COLLECTIONS,
    HYBRID_SEARCH_ENABLED,
    HYBRID_SPARSE_TOP_N,
)

ExtraFilters = Callable[[Any], Any]  # Select -> Select
RowFilter = Callable[[dict], bool]

# When a row_filter (e.g. GOAP_CANDIDATE_DENYLIST) is active, Python-side
# filtering is applied after the SQL LIMIT fetch.  A heavy denylist can
# under-fill below top_k.  Over-fetch by this factor so enough candidates
# survive.  Only activated when row_filter is not None → zero perf impact
# on non-denylist paths.  Capped at _DENSE_OVERFETCH_CAP to avoid runaway.
_DENSE_OVERFETCH_FACTOR: int = 4
_DENSE_OVERFETCH_CAP: int = 200


def rrf_fuse(dense: list[dict], sparse: list[dict], key_fn: Callable[[dict], Hashable], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion over dense and sparse candidate lists.

    Score for each item present at 1-indexed rank r is 1 / (k + r), summed
    across both lists. ``key_fn`` identifies "the same candidate" across the
    two lists — callers key by primary id, or a composite tuple (routes use
    ``(plugin_id, operation_id)``) when there's no single id column.
    """
    scores: dict[Hashable, float] = {}
    best_candidate: dict[Hashable, dict] = {}

    for rank, cand in enumerate(dense, start=1):
        key = key_fn(cand)
        scores[key] = scores.get(key, 0.0) + (1.0 / (k + rank))
        if key not in best_candidate:
            best_candidate[key] = dict(cand)

    for rank, cand in enumerate(sparse, start=1):
        key = key_fn(cand)
        scores[key] = scores.get(key, 0.0) + (1.0 / (k + rank))
        if key not in best_candidate:
            best_candidate[key] = dict(cand)

    fused: list[dict] = []
    for key, fused_score in scores.items():
        item = dict(best_candidate[key])
        item["score"] = float(fused_score)
        fused.append(item)

    fused.sort(key=lambda x: x["score"], reverse=True)
    return fused


def normalize_rrf_scores(fused: list[dict], k: int, n_lists: int) -> list[dict]:
    """Scale raw RRF scores into ~[0,1] so the confidence gate keeps its
    cosine-era thresholds. Constant per-query factor -> ordering preserved."""
    if n_lists < 1:
        return fused
    factor = (k + 1) / n_lists  # 1 / theoretical_max
    for item in fused:
        item["score"] = float(item.get("score", 0.0)) * factor
    return fused


@dataclass(frozen=True)
class SearchSpec:
    """Static description of one embedding table's searchable shape.

    Built once per store module (module-level constant) — per-call filters
    (tenant_id, collection, conversation_id, ...) are passed to ``search()``
    separately via ``extra_filters``/``row_filter`` so this stays immutable.
    """

    name: str  # collection id for the HYBRID_SEARCH_COLLECTIONS override, e.g. "routes", "memory"
    model: type
    select_cols: Sequence[Any]
    embedding_col: Any
    model_col: Any
    key_fn: Callable[[dict], Hashable]
    row_to_dict: Callable[[Any], dict]
    search_doc_col: Any | None = None  # None -> table has no tsvector column, dense-only always
    vector_transform: Callable[[list[float]], list[float]] | None = None  # e.g. unity's dimension coercion


async def _dense(
    spec: SearchSpec,
    vector: list[float],
    top_k: int,
    model_id: str,
    extra_filters: ExtraFilters | None,
    row_filter: RowFilter | None,
) -> list[dict]:
    # When a Python-side row_filter is active, over-fetch at the SQL level so
    # denylist-heavy queries still return up to top_k items after filtering.
    # When row_filter is None the limit is exactly top_k (no change).
    sql_limit = (
        min(top_k * _DENSE_OVERFETCH_FACTOR, _DENSE_OVERFETCH_CAP)
        if row_filter is not None
        else top_k
    )
    async with get_async_session() as session:
        stmt = (
            select(*spec.select_cols, (1 - spec.embedding_col.cosine_distance(vector)).label("similarity"))
            .where(spec.embedding_col.is_not(None))
            .where(spec.model_col == model_id)
            .order_by(spec.embedding_col.cosine_distance(vector))
            .limit(sql_limit)
        )
        if extra_filters:
            stmt = extra_filters(stmt)
        rows = (await session.execute(stmt)).all()
        candidates: list[dict] = []
        for row in rows:
            d = spec.row_to_dict(row)
            if row_filter and not row_filter(d):
                continue
            d["score"] = float(row.similarity)
            candidates.append(d)
        return candidates


async def _sparse(
    spec: SearchSpec,
    query: str,
    top_k: int,
    model_id: str,
    extra_filters: ExtraFilters | None,
    row_filter: RowFilter | None,
) -> list[dict]:
    if spec.search_doc_col is None:
        return []
    async with get_async_session() as session:
        tsquery = func.websearch_to_tsquery("english", query)
        stmt = (
            select(*spec.select_cols, func.ts_rank_cd(spec.search_doc_col, tsquery).label("similarity"))
            .where(spec.search_doc_col.op("@@")(tsquery))
            .where(spec.model_col == model_id)
            .order_by(sa_text("similarity DESC"))
            .limit(top_k)
        )
        if extra_filters:
            stmt = extra_filters(stmt)
        rows = (await session.execute(stmt)).all()
        candidates: list[dict] = []
        for row in rows:
            d = spec.row_to_dict(row)
            if row_filter and not row_filter(d):
                continue
            d["score"] = float(row.similarity)
            candidates.append(d)
        return candidates


async def search(
    spec: SearchSpec,
    query: str,
    sel: dict,
    top_k: int,
    *,
    extra_filters: ExtraFilters | None = None,
    row_filter: RowFilter | None = None,
) -> list[dict]:
    """Embed ``query`` and search ``spec``'s table — hybrid (dense+sparse RRF)
    or dense-only, decided by the global ``HYBRID_SEARCH_ENABLED`` flag with a
    per-collection override in ``HYBRID_SEARCH_COLLECTIONS[spec.name]``.

    Dense-only automatically when the table has no ``search_doc`` tsvector
    column (``spec.search_doc_col is None``) regardless of config.
    """
    model_id = model_id_for(sel)
    vector = await embed_query_with(sel, query)
    if spec.vector_transform:
        vector = spec.vector_transform(vector)

    hybrid_on = HYBRID_SEARCH_COLLECTIONS.get(spec.name, HYBRID_SEARCH_ENABLED)

    if not hybrid_on or spec.search_doc_col is None:
        dense_res = await _dense(spec, vector, top_k, model_id, extra_filters, row_filter)
        return dense_res[:top_k]

    dense_res, sparse_res = await asyncio.gather(
        _dense(spec, vector, HYBRID_DENSE_TOP_N, model_id, extra_filters, row_filter),
        _sparse(spec, query, HYBRID_SPARSE_TOP_N, model_id, extra_filters, row_filter),
    )

    fused = rrf_fuse(dense_res, sparse_res, spec.key_fn, k=HYBRID_RRF_K)
    n_lists = (1 if dense_res else 0) + (1 if sparse_res else 0)
    fused = normalize_rrf_scores(fused, k=HYBRID_RRF_K, n_lists=n_lists)
    # Cap by both caller top_k and configured fused_top_n pool size.
    return fused[: min(top_k, HYBRID_FUSED_TOP_N)]
