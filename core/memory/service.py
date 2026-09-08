"""Core semantic memory service — MemoryRegistry + dual backends.

Memory backend → memory_content_vectors (agent notes, recipes, anti-patterns).
Search backend → search_content_vectors (only for registered search namespaces).
Harness instruction updates go to server_settings JSON (not only vectors).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("whiskers")

from core.memory.collections import (
    ALLOWED_TIERS,
    COLLECTION_CORE_INSTRUCTIONS,
    COLLECTION_GLOBAL,
    COLLECTION_PLAN_ANTI,
    COLLECTION_PLAN_RECIPES,
)
from core.memory.registry import get_memory_registry
from db_layer.memory_content_vectors_store import (
    add_memory_content_vector,
    delete_memory_content_vector,
    list_memory_content_vectors,
    search_memory_content_vectors,
)
from db_layer.search_content_vectors_store import (
    add_search_content_vector,
    delete_search_content_vector,
    list_search_content_vectors,
    search_search_content_vectors,
)


def _is_harness_instruction_write(
    collection: str,
    tags: list[str] | None,
    tier: str,
    metadata_extra: dict[str, Any] | None,
) -> bool:
    if collection == COLLECTION_CORE_INSTRUCTIONS:
        return True
    if tier == "harness":
        return True
    tags = tags or []
    if "harness_instruction" in tags:
        return True
    if metadata_extra and metadata_extra.get("kind") == "harness_instruction":
        return True
    return False


async def save_memory(
    content: str,
    *,
    tenant_id: int,
    tags: list[str] | None = None,
    tier: str = "global",
    collection: str = COLLECTION_GLOBAL,
    metadata_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save semantic memory or upsert a global harness instruction."""
    if not content or not str(content).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["content"],
        }
    if tier not in ALLOWED_TIERS:
        tier = "global"

    # Global harness instruction path (DB JSON)
    if _is_harness_instruction_write(collection, tags, tier, metadata_extra):
        from core.memory.harness_instructions import upsert_instruction

        meta = metadata_extra or {}
        iid = str(
            meta.get("instruction_id")
            or meta.get("id")
            or (tags[0] if tags else "")
            or "custom"
        )
        title = str(meta.get("title") or iid)
        result = await upsert_instruction(
            iid,
            str(content).strip(),
            title=title,
            tags=list(tags or ["harness"]),
            source=str(meta.get("source") or "tool"),
        )
        # Optional vector mirror for RAG discovery
        if result.get("status") == "ok":
            try:
                await add_memory_content_vector(
                    COLLECTION_CORE_INSTRUCTIONS,
                    str(content).strip(),
                    metadata={
                        "tags": list(tags or ["harness"]),
                        "tier": "harness",
                        "kind": "harness_instruction",
                        "instruction_id": result.get("id"),
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                    },
                    tenant_id=tenant_id,
                )
            except Exception:
                # Dual-write to vectors is best-effort; settings write already succeeded.
                logger.debug("memory.service: harness vector dual-write failed", exc_info=True)
        return result

    reg = get_memory_registry()
    ns = reg.get(collection)
    if ns is None:
        return {
            "status": "error",
            "error": "collection_not_registered",
            "collection": collection,
        }
    if not ns.writable:
        return {
            "status": "error",
            "error": "collection_not_writable",
            "collection": collection,
        }

    metadata: dict[str, Any] = {
        "tags": list(tags or []),
        "tier": tier,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    if metadata_extra:
        metadata.update(metadata_extra)

    if ns.backend == "search":
        return await add_search_content_vector(
            collection,
            str(content).strip(),
            metadata=metadata,
            tenant_id=tenant_id,
        )
    return await add_memory_content_vector(
        collection,
        str(content).strip(),
        metadata=metadata,
        tenant_id=tenant_id,
    )


async def search_memory(
    query: str,
    *,
    tenant_id: int,
    top_k: int = 5,
    tag: str = "",
    tier: str = "",
    collection: str = COLLECTION_GLOBAL,
) -> dict[str, Any]:
    """Semantic search over a registered memory or search collection."""
    if not query or not str(query).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["query"],
        }

    reg = get_memory_registry()
    ns = reg.get(collection)
    if ns is None:
        return {
            "status": "error",
            "error": "collection_not_registered",
            "collection": collection,
        }

    limit = top_k * 4 if (tag or tier) else top_k
    if ns.backend == "search":
        results = await search_search_content_vectors(
            str(query).strip(), collection, limit, tenant_id=tenant_id
        )
    else:
        results = await search_memory_content_vectors(
            str(query).strip(), collection, limit, tenant_id=tenant_id
        )

    filtered: list[dict[str, Any]] = []
    for row in results:
        metadata = row.get("metadata") or {}
        row_tags = metadata.get("tags") or []
        row_tier = metadata.get("tier") or "global"
        if (not tag or tag in row_tags) and (not tier or row_tier == tier):
            filtered.append(
                {
                    "id": row["id"],
                    "content": row["content_text"],
                    "tags": row_tags,
                    "tier": row_tier,
                    "similarity": row.get("similarity"),
                    "metadata": metadata,
                    "collection": collection,
                    "backend": ns.backend,
                }
            )

    trimmed = filtered[:top_k]
    return {"status": "ok", "count": len(trimmed), "memories": trimmed}


async def list_memories(
    *,
    tenant_id: int,
    limit: int = 20,
    offset: int = 0,
    tag: str = "",
    collection: str = COLLECTION_GLOBAL,
) -> dict[str, Any]:
    """List memories by creation order for a registered collection."""
    reg = get_memory_registry()
    ns = reg.get(collection)
    if ns is None:
        return {
            "status": "error",
            "error": "collection_not_registered",
            "collection": collection,
        }

    if ns.backend == "search":
        results = await list_search_content_vectors(
            collection, limit, offset, tenant_id=tenant_id
        )
    else:
        results = await list_memory_content_vectors(
            collection, limit, offset, tenant_id=tenant_id
        )

    filtered: list[dict[str, Any]] = []
    for row in results:
        metadata = row.get("metadata") or {}
        row_tags = metadata.get("tags") or []
        row_tier = metadata.get("tier") or "global"
        if not tag or tag in row_tags:
            filtered.append(
                {
                    "id": row["id"],
                    "content": row["content_text"],
                    "tags": row_tags,
                    "tier": row_tier,
                    "created_at": row.get("created_at"),
                    "metadata": metadata,
                    "collection": collection,
                    "backend": ns.backend,
                }
            )
    return {"status": "ok", "count": len(filtered), "memories": filtered}


async def delete_memory(
    memory_id: int,
    *,
    tenant_id: int,
    collection: str | None = COLLECTION_GLOBAL,
) -> dict[str, Any]:
    """Delete a memory by id; registry must allow the collection."""
    coll = collection or COLLECTION_GLOBAL
    reg = get_memory_registry()
    ns = reg.get(coll)
    if ns is None:
        return {
            "status": "error",
            "error": "collection_not_registered",
            "collection": coll,
        }
    if not ns.writable:
        return {
            "status": "error",
            "error": "collection_not_writable",
            "collection": coll,
        }

    if ns.backend == "search":
        success = await delete_search_content_vector(
            memory_id, collection=coll, tenant_id=tenant_id
        )
    else:
        success = await delete_memory_content_vector(
            memory_id, collection=coll, tenant_id=tenant_id
        )
    if success:
        return {"status": "ok", "deleted": True}
    return {"status": "error", "error": "memory_not_found"}


async def save_plan_recipe(
    content: str,
    *,
    tenant_id: int,
    op_ids: list[str],
    plugin_ids: list[str] | None = None,
    goal_summary: str = "",
) -> dict[str, Any]:
    """Persist a successful plan recipe (op chain only — no arg values)."""
    return await save_memory(
        content,
        tenant_id=tenant_id,
        tags=["plan_recipe"],
        tier="global",
        collection=COLLECTION_PLAN_RECIPES,
        metadata_extra={
            "op_ids": list(op_ids or []),
            "plugin_ids": list(plugin_ids or []),
            "goal_summary": (goal_summary or "")[:500],
            "kind": "plan_recipe",
        },
    )


async def save_anti_pattern(
    content: str,
    *,
    tenant_id: int,
    plan_ops: list[str],
    outcome: str = "error",
    detail: str = "",
) -> dict[str, Any]:
    """Persist a failed plan sequence to avoid on similar future queries."""
    return await save_memory(
        content,
        tenant_id=tenant_id,
        tags=["plan_anti_pattern"],
        tier="global",
        collection=COLLECTION_PLAN_ANTI,
        metadata_extra={
            "plan_ops": list(plan_ops or []),
            "outcome": (outcome or "error")[:64],
            "detail": (detail or "")[:500],
            "kind": "plan_anti_pattern",
        },
    )


async def search_plan_recipes(
    query: str,
    *,
    tenant_id: int,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """Semantic search over plan_recipes; returns normalized recipe dicts."""
    res = await search_memory(
        query,
        tenant_id=tenant_id,
        top_k=top_k,
        collection=COLLECTION_PLAN_RECIPES,
    )
    if res.get("status") != "ok":
        return []
    out: list[dict[str, Any]] = []
    for m in res.get("memories") or []:
        meta = m.get("metadata") or {}
        out.append(
            {
                "id": m.get("id"),
                "content": m.get("content") or "",
                "op_ids": list(meta.get("op_ids") or []),
                "plugin_ids": list(meta.get("plugin_ids") or []),
                "similarity": float(m.get("similarity") or 0.0),
                "metadata": meta,
            }
        )
    return out


async def search_anti_patterns(
    query: str,
    *,
    tenant_id: int,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """Semantic search over plan_anti_patterns."""
    res = await search_memory(
        query,
        tenant_id=tenant_id,
        top_k=top_k,
        collection=COLLECTION_PLAN_ANTI,
    )
    if res.get("status") != "ok":
        return []
    out: list[dict[str, Any]] = []
    for m in res.get("memories") or []:
        meta = m.get("metadata") or {}
        out.append(
            {
                "id": m.get("id"),
                "content": m.get("content") or "",
                "plan_ops": list(meta.get("plan_ops") or []),
                "outcome": meta.get("outcome") or "",
                "detail": meta.get("detail") or "",
                "similarity": float(m.get("similarity") or 0.0),
                "metadata": meta,
            }
        )
    return out
