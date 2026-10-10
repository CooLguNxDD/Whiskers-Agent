"""S03 world service and thin, unregistered callable adapters for S04 assembly.

Neither adapter accepts tenant identity from a request or registers a public route.
S04 supplies an AuthorizedWorld capability after authentication/world authorization.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import h3

from plugins.world_semantic_plugin.hexmath import cell_center_meters
from plugins.world_semantic_plugin.stores import world_hex_store
from plugins.world_semantic_plugin.stores.unity_world_vectors_store import resolve_unity_world_embedding
from plugins.world_semantic_plugin.world_documents import AuthorizedWorld, normalize_document, require_context


async def index_world(payload: dict, *, context: AuthorizedWorld,
                      trusted_root: Path | None = None) -> dict:
    """Validate one inspect/snapshot pair and queue a revision; never call Unity or a provider."""
    context = require_context(context)
    selection = await resolve_unity_world_embedding()
    model_id, _ = world_hex_store.validate_selection(selection)
    document = await asyncio.to_thread(normalize_document, context, payload, model_id, trusted_root=trusted_root)
    status = await world_hex_store.reserve_document(document)
    return {"status": status, "world_id": context.world_id, "hex_id": document.hex_id,
            "revision": document.revision, "content_hash": document.content_hash,
            "model_id": model_id, "has_image": document.media is not None}


async def search_world(query: str, k: int = 10, *, context: AuthorizedWorld) -> list[dict]:
    """Search indexed world hexes in the selected joint space; centers are Unity [x,z] meters."""
    context = require_context(context)
    if not isinstance(query, str) or not query.strip() or len(query) > 10000:
        raise ValueError("query must be nonempty text within 10000 characters")
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    k = min(k, world_hex_store.MAX_K)
    selection = await resolve_unity_world_embedding()
    world_hex_store.validate_selection(selection)
    hits = await world_hex_store.search_documents(context, query.strip(), selection, k)
    results, seen = [], set()
    for hit in hits:
        hex_id = hit["doc_id"]
        if hex_id in seen:
            continue
        if not h3.is_valid_cell(hex_id) or h3.get_resolution(hex_id) != context.projection.base_res:
            raise ValueError("index contains invalid world hex")
        seen.add(hex_id)
        results.append({"hex_id": hex_id,
                        "center_pos": list(cell_center_meters(hex_id, context.projection)),
                        "center_frame": "unity_xz_meters", "summary": hit["content_text"],
                        "score": float(hit["score"])})
    return results[:k]
