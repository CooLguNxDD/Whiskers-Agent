"""MCP tools: spatial queries + encoded context (steps 1–3)."""

from __future__ import annotations

from core.context import mcp
from plugins.world_semantic_plugin.encoder import encode
from plugins.world_semantic_plugin.hexmath import (
    cell_center_meters,
    projection_from_world_row,
)
from plugins.world_semantic_plugin.stores import (
    describe_region,
    get_hex,
    get_world,
    list_objects_in_hex,
    query_objects_near,
)


@mcp.tool(
    title="query_context",
    tags={"world_semantic_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def query_context(
    world_id: str,
    task: str = "",
    anchor_hex: str = "",
    anchor_name: str = "",
    k: int = 2,
    name_contains: str = "",
    direction: str = "",
    limit: int = 50,
    token_budget: int = 800,
    raw: bool = False,
) -> dict:
    """Query spatial context around an anchor hex or named object.

    raw=true  → step-1 object list (SQL + kRing).
    raw=false → deterministic encoder grammar (step 2+), with dirty summary
                refresh + optional embedding rank (step 3).
    """
    if not world_id or not world_id.strip():
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["world_id"]}

    wid = world_id.strip()
    if raw:
        return await query_objects_near(
            wid,
            anchor_hex=anchor_hex.strip() or None,
            anchor_name=anchor_name.strip() or None,
            k=max(0, int(k)),
            name_contains=name_contains.strip() or None,
            direction=direction.strip() or None,
            limit=max(1, min(int(limit), 500)),
        )

    return await encode(
        task or name_contains or "describe region",
        wid,
        anchor_hex=anchor_hex.strip() or None,
        anchor_name=anchor_name.strip() or None,
        k=max(0, int(k)),
        direction=direction.strip(),
        token_budget=max(50, int(token_budget)),
        use_embeddings=True,
        refresh_dirty=True,
    )


@mcp.tool(
    title="describe_region",
    tags={"world_semantic_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def describe_region_tool(
    world_id: str,
    anchor_name: str = "",
    anchor_hex: str = "",
    k: int = 2,
    direction: str = "",
) -> dict:
    """Describe objects near an anchor (e.g. what's north of the player camp)."""
    if not world_id or not world_id.strip():
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["world_id"]}

    return await describe_region(
        world_id.strip(),
        anchor_name=anchor_name.strip() or None,
        anchor_hex=anchor_hex.strip() or None,
        k=max(0, int(k)),
        direction=direction.strip() or None,
    )


@mcp.tool(
    title="get_hex",
    tags={"world_semantic_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_hex_tool(world_id: str, hex_id: str) -> dict:
    """Fetch a single hex row (counts, dirty, summary, lease fields).

    Includes ``center_pos`` ``[x, z]`` world meters from the world's H3 projection.
    """
    if not world_id or not hex_id:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": [f for f in ("world_id", "hex_id") if not locals().get(f)],
        }
    wid = world_id.strip()
    hid = hex_id.strip()
    result = await get_hex(wid, hid)
    if result.get("status") != "ok":
        return result

    world = await get_world(wid)
    proj = projection_from_world_row(world)
    try:
        cx, cz = cell_center_meters(hid, proj)
        center = [cx, cz]
    except Exception:
        center = None

    hex_row = result.get("hex")
    if isinstance(hex_row, dict) and center is not None:
        hex_row = dict(hex_row)
        hex_row["center_pos"] = center
        result = {**result, "hex": hex_row, "center_pos": center}
    elif center is not None:
        result = {**result, "center_pos": center}
    return result


@mcp.tool(
    title="list_objects_in_hex",
    tags={"world_semantic_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def list_objects_in_hex_tool(
    world_id: str,
    hex_id: str,
    limit: int = 200,
    y_layer: int | None = None,
) -> dict:
    """List indexed objects inside one H3 cell.

    Optional ``y_layer`` filters to a single 3D hex prism layer
    (``y_layer = floor(pos.y / world.layer_height)``).
    """
    if not world_id or not hex_id:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": [f for f in ("world_id", "hex_id") if not locals().get(f)],
        }
    return await list_objects_in_hex(
        world_id.strip(),
        hex_id.strip(),
        y_layer=y_layer,
        limit=max(1, min(int(limit), 1000)),
    )


@mcp.tool(
    title="search_world_vectors",
    tags={"world_semantic_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def search_world_vectors(
    world_id: str,
    query: str,
    top_k: int = 10,
    doc_kind: str = "",
) -> dict:
    """Semantic search over unity_world_vectors (object + hex RAG docs).

    Isolated stack: does not use content_vectors or route_embeddings.
    doc_kind: '' (all), 'object', 'hex', or 'hex_layer'.
    """
    if not world_id or not str(world_id).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["world_id"],
        }
    if not query or not str(query).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["query"],
        }

    from plugins.world_semantic_plugin.stores.unity_world_vectors_store import search_unity_world_vectors

    kind = (doc_kind or "").strip().lower() or None
    if kind and kind not in ("object", "hex", "hex_layer"):
        return {
            "status": "error",
            "error": "invalid_doc_kind",
            "allowed": ["object", "hex", "hex_layer", ""],
        }

    hits = await search_unity_world_vectors(
        query.strip(),
        world_id.strip(),
        top_k=max(1, min(int(top_k), 50)),
        doc_kind=kind,
    )
    return {
        "status": "ok",
        "world_id": world_id.strip(),
        "query": query.strip(),
        "count": len(hits),
        "results": hits,
    }
