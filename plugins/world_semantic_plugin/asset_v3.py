"""WorldGen v3 asset library: pack ingest, per-view + caption vectors, max-sim scoring.

Pure logic and service glue only; SQL lives in `stores/asset_store.py`. Every function that
touches persistence takes an optional `store` so tests can inject an in-memory double.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import math

from db_layer.embeddings.multimodal import (
    EmbeddingInput, EmbeddingMedia, document_text, query_text, validate_embedding_media,
)
from plugins.world_semantic_plugin import asset_index as api
from plugins.world_semantic_plugin import asset_packs

logger = logging.getLogger("whiskers.plugins.world_semantic")
SCHEMA = "v3"
EMBED_BATCH = 8
EMBED_CONCURRENCY = 4
MAX_TRIS_LIMIT = 2_000_000_000
DISTRIBUTION_FILTERS = frozenset({"shared", "local", "any"})
_FILTER_KEYS = ("role", "biome", "licence", "distribution", "pack", "max_tris")


def _store(store):
    if store is not None:
        return store
    from plugins.world_semantic_plugin.stores import asset_store
    return asset_store


def decide_stage(existing_hash: str | None, existing_status: str | None, new_hash: str,
                 queue_active: bool) -> str:
    """`indexed`/`pending` are no-ops for an unchanged hash; anything else (re)embeds."""
    if existing_hash == new_hash:
        if existing_status == "indexed":
            return "indexed"
        if existing_status == "enqueued" and queue_active:
            return "pending"
    return "enqueue"


# ---------------------------------------------------------------- ingest

async def index_pack_assets(body: dict, *, tenant_id: int, world_id: str, pack_roots: dict,
                            store=None) -> dict:
    """Stage v3 pack records (per-record errors, batch continues); embedding runs in the worker."""
    api.validate_namespace(tenant_id, world_id)
    if not pack_roots:
        raise ValueError("no pack roots configured for this tenant/world")
    sel = await api.resolve_asset_embedding()
    model_id = api.embeddings_core.model_id_for(sel)
    prepared, errors = await asyncio.to_thread(asset_packs.prepare_records, body, pack_roots,
                                               tenant_id=tenant_id, world_id=world_id, model_id=model_id)
    result = {"status": "indexed", "enqueued": 0, "pending": 0, "indexed": 0}
    if prepared:
        result = await _store(store).stage_v3_assets(prepared, tenant_id=tenant_id, world_id=world_id,
                                                     model_id=model_id, dimensions=api.dimensions_for(sel))
    result["errors"] = errors
    result["warnings"] = []
    return result


# ---------------------------------------------------------------- embedding

async def embed_bounded(sel: dict, inputs: list[EmbeddingInput]) -> list[list[float]]:
    """Embed in order through the shared multimodal client, in Semaphore-bounded chunks."""
    gate = asyncio.Semaphore(EMBED_CONCURRENCY)

    async def one(chunk):
        async with gate:
            return await api.embeddings_core.embed_multimodal_with(sel, chunk)

    chunks = [inputs[i:i + EMBED_BATCH] for i in range(0, len(inputs), EMBED_BATCH)]
    vectors = [v for part in await asyncio.gather(*(one(c) for c in chunks)) for v in part]
    if len(vectors) != len(inputs):
        raise ValueError("asset embedding cardinality mismatch")
    return vectors


def asset_inputs(payload: dict) -> list[EmbeddingInput]:
    """Views first (image only, no prefix), then the caption with the document prefix."""
    documents = []
    for view in payload["views"]:
        medium = EmbeddingMedia(base64.b64decode(view["data"], validate=True), view["mime_type"])
        validate_embedding_media(medium, asset_packs.MAX_VIEW_BYTES)
        documents.append(EmbeddingInput(media=medium))
    if payload.get("caption"):
        documents.append(EmbeddingInput(text=document_text(payload["caption"])))
    return documents


async def consume_v3_job(job: dict, *, store=None) -> None:
    """Worker consumer for v3 jobs: one unit vector per view plus one per caption, or a failure."""
    payload, st = job["payload"], _store(store)
    try:
        api.validate_namespace(payload["tenant_id"], payload["world_id"])
        sel = await api.resolve_asset_embedding()
        if api.embeddings_core.model_id_for(sel) != payload["model"] or api.dimensions_for(sel) != payload["dimensions"]:
            raise ValueError("asset embedding selection changed; re-ingest for current model")
        documents = await asyncio.to_thread(asset_inputs, payload)
        vectors = await embed_bounded(sel, documents)
        rows = []
        for index, vector in enumerate(vectors):
            is_caption = index >= len(payload["views"])
            rows.append({"vector_kind": "caption" if is_caption else "view",
                         "view_idx": 0 if is_caption else index,
                         "embedding": api.aggregate_vectors([vector], payload["dimensions"])})
        await st.finish_v3_job(job, rows)
    except Exception:
        # No provider text, bytes or paths in logs or durable errors.
        logger.warning("v3 asset embedding job failed id=%s", job.get("id"))
        await st.finish_v3_job(job, None, failed=True)


# ---------------------------------------------------------------- scoring

def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two same-width vectors (0.0 for a zero vector)."""
    na, nb = math.hypot(*a), math.hypot(*b)
    return 0.0 if na == 0 or nb == 0 else sum(x * y for x, y in zip(a, b)) / (na * nb)


def rank_assets(similarities: dict[str, list[float]]) -> list[dict]:
    """Max over views (caption counts as a view), mean tie-break, then asset_id: deterministic."""
    scored = [{"asset_id": a, "score": max(s), "mean_score": sum(s) / len(s)}
              for a, s in similarities.items() if s]
    return sorted(scored, key=lambda r: (-r["score"], -r["mean_score"], r["asset_id"]))


def normalize_filters(*, role=None, biome=None, licence=None, distribution=None, pack=None,
                      max_tris=None, allow_local: bool = False) -> dict:
    """Validate v3 filters; local/any require a local-capable namespace (never default)."""
    out = {}
    for name, value in (("role", role), ("biome", biome), ("licence", licence), ("pack", pack)):
        if value is not None:
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"invalid {name} filter")
            out[name] = value.strip()
    if max_tris is not None:
        if type(max_tris) is not int or not 0 <= max_tris <= MAX_TRIS_LIMIT:
            raise ValueError("max_tris must be a nonnegative integer")
        out["max_tris"] = max_tris
    distribution = "shared" if distribution is None else distribution
    if distribution not in DISTRIBUTION_FILTERS:
        raise ValueError("distribution must be shared, local or any")
    if distribution != "shared" and not allow_local:
        raise PermissionError("local assets are not available for this tenant context")
    out["distribution"] = distribution
    return out


def filter_matches(columns: dict, filters: dict) -> bool:
    """Reference predicate for the store's SQL filters (used by in-memory doubles)."""
    for name, column in (("role", "role"), ("biome", "biome"), ("licence", "licence"), ("pack", "pack_id")):
        if name in filters and columns.get(column) != filters[name]:
            return False
    if "max_tris" in filters and not (columns.get("tri_count") is not None
                                      and columns["tri_count"] <= filters["max_tris"]):
        return False
    wanted = filters.get("distribution", "shared")
    return wanted == "any" or (columns.get("distribution") or "shared") == wanted


def v3_engaged(filter_args: dict, roots: dict) -> bool:
    """v3 scoring runs when any v3 filter is supplied or the namespace has pack roots configured."""
    return bool(roots) or any(filter_args.get(k) is not None for k in _FILTER_KEYS)


# ---------------------------------------------------------------- search

async def search_pack_assets(query: str | None, image: dict | None, *, tenant_id: int, world_id: str,
                             kind: str | None, tags: list[str], k: int, filters: dict, store=None) -> list[dict]:
    """Embed the query (query prefix for text, none for images) and score max-sim in the store."""
    sel = await api.resolve_asset_embedding()
    if image is None:
        document = EmbeddingInput(text=query_text(api._string(query, "query", 8192)))
    else:
        document = EmbeddingInput(media=await asyncio.to_thread(api.decode_image, image))
    vectors = await api.embeddings_core.embed_multimodal_with(sel, [document])
    if len(vectors) != 1:
        raise ValueError("asset query embedding cardinality mismatch")
    vector = api.aggregate_vectors(vectors, api.dimensions_for(sel))
    hits = await _store(store).search_asset_vectors(
        vector, tenant_id=tenant_id, world_id=world_id, model_id=api.embeddings_core.model_id_for(sel),
        k=k, filters=filters, kind=kind, tags=tags)
    # Re-assert the deterministic order and k regardless of the store implementation.
    hits = sorted(hits, key=lambda r: (-r["score"], -r.get("mean_score", r["score"]), r["asset_id"]))
    return hits[:k]
