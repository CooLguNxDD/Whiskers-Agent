"""Memory tools — MCP façade over core.memory (MemoryRegistry + dual backends).

Agents and MCP hosts call these tools; the graph harness also writes/reads
the same memory backend via ``core.memory``. Tenant scope comes from
``current_tenant_id``. Global harness instructions update via
``save_memory(collection="core_instructions")``.
"""

from __future__ import annotations

import logging
from typing import Any

from core.context import current_tenant_id, mcp
from core.memory import (
    COLLECTION_CORE_INSTRUCTIONS,
    COLLECTION_GLOBAL,
    MCP_DELETE_COLLECTIONS,
    MCP_READ_COLLECTIONS,
    MCP_WRITE_COLLECTIONS,
    delete_memory as core_delete_memory,
    get_memory_registry,
    list_memories as core_list_memories,
    resolve_mcp_collection,
    save_memory as core_save_memory,
    search_anti_patterns as core_search_anti_patterns,
    search_memory as core_search_memory,
    search_plan_recipes as core_search_plan_recipes,
)

logger = logging.getLogger("whiskers.memory_plugin")


def _tenant_id() -> int:
    """Resolve active tenant; ContextVar defaults to 1."""
    tid = current_tenant_id.get()
    try:
        return int(tid) if tid is not None else 1
    except (TypeError, ValueError):
        return 1


def _collection_error(code: str, *, allowed: frozenset[str]) -> dict[str, Any]:
    return {
        "status": "error",
        "error": code,
        "allowed_collections": sorted(allowed),
    }


def _allow_write_collection(collection: str) -> bool:
    if collection in MCP_WRITE_COLLECTIONS:
        return True
    ns = get_memory_registry().get(collection)
    return bool(ns and ns.backend == "memory" and ns.writable and ns.owner != "core")


def _allow_read_collection(collection: str) -> bool:
    if collection in MCP_READ_COLLECTIONS:
        return True
    ns = get_memory_registry().get(collection)
    return ns is not None


@mcp.tool(
    title="save_memory",
    tags={"memory_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def save_memory(
    content: str,
    tags: list[str] | None = None,
    tier: str = "global",
    collection: str = COLLECTION_GLOBAL,
) -> dict[str, Any]:
    """Save semantic memory for later recall (tenant-scoped).

    Stores free-text notes, preferences, or workflow facts the agent should
    remember across turns. Default collection is ``global_memory``. Plan
    recipes / anti-patterns are written by the core harness, not this tool.

    Args:
        content: Text to remember (required, non-empty).
        tags: Optional labels for post-filter on search/list.
        tier: ``global`` | ``project`` | ``session`` (invalid values → global).
        collection: Must be an MCP write-allowed collection (default global_memory).
    """
    coll, err = resolve_mcp_collection(
        collection, allowed=MCP_WRITE_COLLECTIONS, default=COLLECTION_GLOBAL
    )
    if err or not coll:
        # Allow plugin-registered memory namespaces
        raw = (collection or COLLECTION_GLOBAL).strip() or COLLECTION_GLOBAL
        if _allow_write_collection(raw):
            coll = raw
        else:
            return _collection_error(
                err or "collection_not_allowed", allowed=MCP_WRITE_COLLECTIONS
            )
    try:
        return await core_save_memory(
            content,
            tenant_id=_tenant_id(),
            tags=tags,
            tier=tier,
            collection=coll,
            metadata_extra=(
                {"kind": "harness_instruction", "source": "tool"}
                if coll == COLLECTION_CORE_INSTRUCTIONS
                else None
            ),
        )
    except Exception:
        logger.error("save_memory failed", exc_info=True)
        return {
            "status": "error",
            "error": "save_failed",
            "message": "An internal error occurred while saving memory.",
        }


@mcp.tool(
    title="search_memory",
    tags={"memory_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def search_memory(
    query: str,
    top_k: int = 5,
    tag: str = "",
    tier: str = "",
    collection: str = COLLECTION_GLOBAL,
) -> dict[str, Any]:
    """Semantic search over persistent memory (tenant-scoped).

    Use before answering when the user refers to prior notes, preferences, or
    saved facts. Optional ``tag`` / ``tier`` filters apply after vector search.

    Args:
        query: Natural-language similarity query (required).
        top_k: Max hits (clamped 1–50).
        tag: Keep only memories that include this tag.
        tier: Keep only memories with this tier.
        collection: ``global_memory`` (default), ``plan_recipes``, or
            ``plan_anti_patterns``.
    """
    coll, err = resolve_mcp_collection(
        collection, allowed=MCP_READ_COLLECTIONS, default=COLLECTION_GLOBAL
    )
    if err or not coll:
        raw = (collection or COLLECTION_GLOBAL).strip() or COLLECTION_GLOBAL
        if _allow_read_collection(raw):
            coll = raw
        else:
            return _collection_error(
                err or "collection_not_allowed", allowed=MCP_READ_COLLECTIONS
            )
    try:
        k = max(1, min(int(top_k or 5), 50))
    except (TypeError, ValueError):
        k = 5
    try:
        return await core_search_memory(
            query,
            tenant_id=_tenant_id(),
            top_k=k,
            tag=tag or "",
            tier=tier or "",
            collection=coll,
        )
    except Exception:
        logger.error("search_memory failed", exc_info=True)
        return {
            "status": "error",
            "error": "search_failed",
            "message": "An internal error occurred while searching memory.",
        }


@mcp.tool(
    title="list_memories",
    tags={"memory_plugin", "read"},
    annotations={"readOnlyHint": True},
)
async def list_memories(
    limit: int = 20,
    offset: int = 0,
    tag: str = "",
    collection: str = COLLECTION_GLOBAL,
) -> dict[str, Any]:
    """List memories newest-first (tenant-scoped).

    Prefer ``search_memory`` for semantic recall; use this for browsing or
    admin inspection.

    Args:
        limit: Page size (default 20).
        offset: Skip N rows.
        tag: Optional tag filter.
        collection: Read-allowed collection (default global_memory).
    """
    coll, err = resolve_mcp_collection(
        collection, allowed=MCP_READ_COLLECTIONS, default=COLLECTION_GLOBAL
    )
    if err or not coll:
        return _collection_error(err or "collection_not_allowed", allowed=MCP_READ_COLLECTIONS)
    try:
        lim = max(1, min(int(limit or 20), 100))
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        lim, off = 20, 0
    try:
        return await core_list_memories(
            tenant_id=_tenant_id(),
            limit=lim,
            offset=off,
            tag=tag or "",
            collection=coll,
        )
    except Exception:
        logger.error("list_memories failed", exc_info=True)
        return {
            "status": "error",
            "error": "list_failed",
            "message": "An internal error occurred while listing memories.",
        }


@mcp.tool(
    title="delete_memory",
    tags={"memory_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def delete_memory(
    memory_id: int,
    collection: str = COLLECTION_GLOBAL,
) -> dict[str, Any]:
    """Delete a memory by id (tenant-scoped).

    Only ``global_memory`` rows are deletable via MCP. Harness plan collections
    are managed by the core harness, not agents.

    Args:
        memory_id: Row id from search/list.
        collection: Must be delete-allowed (default global_memory).
    """
    coll, err = resolve_mcp_collection(
        collection, allowed=MCP_DELETE_COLLECTIONS, default=COLLECTION_GLOBAL
    )
    if err or not coll:
        return _collection_error(err or "collection_not_allowed", allowed=MCP_DELETE_COLLECTIONS)
    try:
        mid = int(memory_id)
    except (TypeError, ValueError):
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["memory_id"],
        }
    try:
        return await core_delete_memory(
            mid,
            tenant_id=_tenant_id(),
            collection=coll,
        )
    except Exception:
        logger.error("delete_memory failed", exc_info=True)
        return {
            "status": "error",
            "error": "delete_failed",
            "message": "An internal error occurred while deleting memory.",
        }


@mcp.tool(
    title="search_plan_recipes",
    tags={"memory_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def search_plan_recipes(query: str, top_k: int = 3) -> dict[str, Any]:
    """Search learned successful plan recipes (list→get chains, etc.).

    Recipes are written automatically by the core harness after successful
    multi-step runs. Agents may read them to explain prior workflows; planning
    soft-apply still happens inside the harness, not only via this tool.
    """
    if not query or not str(query).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["query"],
        }
    try:
        k = max(1, min(int(top_k or 3), 20))
    except (TypeError, ValueError):
        k = 3
    try:
        recipes = await core_search_plan_recipes(
            str(query).strip(),
            tenant_id=_tenant_id(),
            top_k=k,
        )
        return {"status": "ok", "count": len(recipes), "recipes": recipes}
    except Exception:
        logger.error("search_plan_recipes failed", exc_info=True)
        return {
            "status": "error",
            "error": "search_failed",
            "message": "An internal error occurred while searching plan recipes.",
        }


@mcp.tool(
    title="search_anti_patterns",
    tags={"memory_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def search_anti_patterns(query: str, top_k: int = 3) -> dict[str, Any]:
    """Search learned failed plan sequences to avoid.

    Anti-patterns are written by the core harness on terminal failures / identical
    replan loops. Useful for diagnostics; the planner also injects them into
    harness context automatically when RAG is enabled.
    """
    if not query or not str(query).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["query"],
        }
    try:
        k = max(1, min(int(top_k or 3), 20))
    except (TypeError, ValueError):
        k = 3
    try:
        items = await core_search_anti_patterns(
            str(query).strip(),
            tenant_id=_tenant_id(),
            top_k=k,
        )
        return {"status": "ok", "count": len(items), "anti_patterns": items}
    except Exception:
        logger.error("search_anti_patterns failed", exc_info=True)
        return {
            "status": "error",
            "error": "search_failed",
            "message": "An internal error occurred while searching anti-patterns.",
        }
