"""MCP tools: multimodal asset and world semantic indexing and search.

Registered with tags 'world_semantic_plugin' and 'read' / 'write'.
Delegates to S02 asset_adapters and S03 world_adapters / world_index.
"""
from __future__ import annotations

from core.context import mcp
from plugins.world_semantic_plugin import asset_adapters, world_adapters


@mcp.tool(
    name="search_assets",
    tags={"world_semantic_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def search_assets(
    world_id: str,
    query: str | None = None,
    image: dict | None = None,
    kind: str | None = None,
    tags: list[str] | None = None,
    k: int = 10,
) -> list[dict]:
    """Multimodal search over worldgen assets by text query or inline image.

    query: text description (e.g. 'stone bridge over a river')
    image: inline image object {'mime_type': 'image/png'|'image/jpeg', 'data': '<base64>'}
    kind: optional filter ('prop', 'block', 'texture', 'audio', 'stamp')
    tags: optional tag list (AND matched)
    k: top-k results (1 <= k <= 100, default 10)
    """
    return await asset_adapters.search_assets(
        world_id=world_id,
        query=query,
        image=image,
        kind=kind,
        tags=tags,
        k=k,
    )


@mcp.tool(
    name="index_assets",
    tags={"world_semantic_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def index_assets(
    world_id: str,
    index: dict,
) -> dict:
    """Stage and enqueue WorldGen asset library (versioned craft-v3 JSON) for embedding.

    index: craft-v3 asset library payload {'version': 1, 'assets': [...]}
    Reference image/audio files are read from server-configured trusted ingestion root.
    """
    return await asset_adapters.index_assets(
        world_id=world_id,
        index=index,
    )


@mcp.tool(
    name="search_world",
    tags={"world_semantic_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def search_world(
    world_id: str,
    query: str,
    k: int = 10,
) -> list[dict]:
    """Semantic search over indexed world hexes by meaning; centers labelled in Unity XZ meters.

    query: text description (e.g. 'lake near a village')
    k: top-k results (1 <= k <= 50, default 10)
    """
    return await world_adapters.search_world(
        world_id=world_id,
        query=query,
        k=k,
    )


@mcp.tool(
    name="index_world",
    tags={"world_semantic_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def index_world(
    world_id: str,
    payload: dict,
) -> dict:
    """Ingest and enqueue one validated Unity inspect/snapshot envelope for semantic indexing.

    payload: inspect and snapshot data with revision and snapshot-center evidence.
    """
    return await world_adapters.index_world(
        world_id=world_id,
        payload=payload,
    )
