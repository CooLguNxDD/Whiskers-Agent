"""Semantic content search tools for the search_plugin (search engine backend)."""

import logging
from typing import Any

from core.context import current_tenant_id, mcp
from db_layer.search_content_vectors_store import (
    add_search_content_vector,
    search_search_content_vectors,
)

logger = logging.getLogger("whiskers.search_plugin")


@mcp.tool(
    title="semantic_index",
    tags={"search_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def semantic_index(
    content: str, collection: str, metadata: dict | None = None
) -> dict[str, Any]:
    """Embed and store content in the search index (``search_content_vectors``)."""
    if not content.strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["content"],
        }
    if not collection.strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["collection"],
        }
    tenant_id = current_tenant_id.get()
    try:
        return await add_search_content_vector(
            collection=collection,
            content_text=content,
            metadata=metadata,
            tenant_id=tenant_id,
        )
    except Exception:
        logger.error("semantic_index failed", exc_info=True)
        return {
            "status": "error",
            "error": "index_failed",
            "message": "An internal error occurred during the index operation.",
        }


@mcp.tool(
    title="semantic_search",
    tags={"search_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def semantic_search(query: str, collection: str, top_k: int = 5) -> dict[str, Any]:
    """Semantic similarity search over the search index (tenant-scoped)."""
    if not query.strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["query"],
        }
    if not collection.strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["collection"],
        }
    clamped = max(1, min(int(top_k), 50))
    tenant_id = current_tenant_id.get()
    try:
        results = await search_search_content_vectors(
            query=query,
            collection=collection,
            top_k=clamped,
            tenant_id=tenant_id,
        )
        return {
            "status": "ok",
            "collection": collection,
            "count": len(results),
            "results": results,
        }
    except Exception:
        logger.error("semantic_search failed", exc_info=True)
        return {
            "status": "error",
            "error": "search_failed",
            "message": "An internal error occurred during the search operation.",
        }
