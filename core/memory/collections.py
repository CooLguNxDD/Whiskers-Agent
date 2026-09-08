"""Named collections and MCP allowlists for core memory."""

from __future__ import annotations

COLLECTION_GLOBAL = "global_memory"
COLLECTION_PLAN_RECIPES = "plan_recipes"
COLLECTION_PLAN_ANTI = "plan_anti_patterns"
COLLECTION_CORE_INSTRUCTIONS = "core_instructions"

ALLOWED_TIERS = frozenset({"global", "project", "session", "harness"})

# Collections agents may read via memory_plugin MCP tools.
MCP_READ_COLLECTIONS = frozenset(
    {
        COLLECTION_GLOBAL,
        COLLECTION_PLAN_RECIPES,
        COLLECTION_PLAN_ANTI,
        COLLECTION_CORE_INSTRUCTIONS,
    }
)

# Collections agents may write via memory_plugin.
MCP_WRITE_COLLECTIONS = frozenset(
    {
        COLLECTION_GLOBAL,
        COLLECTION_CORE_INSTRUCTIONS,  # upserts global harness JSON via save_memory
    }
)

# Collections agents may delete via memory_plugin.
MCP_DELETE_COLLECTIONS = frozenset({COLLECTION_GLOBAL})


def resolve_mcp_collection(
    collection: str | None,
    *,
    allowed: frozenset[str],
    default: str = COLLECTION_GLOBAL,
) -> tuple[str | None, str | None]:
    """Resolve a collection name for MCP tools.

    Returns ``(collection, None)`` on success or ``(None, error_code)`` on deny.
    """
    raw = (collection or "").strip()
    name = raw or default
    if name not in allowed:
        # Allow plugin-registered namespaces that pass the allowlist pattern
        # only when the collection is already in the registry (checked later).
        if name.startswith("plugin:") or "__" in name:
            return name, None  # deferred to registry
        return None, "collection_not_allowed"
    return name, None
