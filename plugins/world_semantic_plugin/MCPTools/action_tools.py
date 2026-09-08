"""L4 placement helpers (step 4) — plan only; spawn via unity-mcp bridge."""

from __future__ import annotations

from core.context import mcp
from plugins.world_semantic_plugin.encoder import encode
from plugins.world_semantic_plugin.hexmath import (
    cell_center_meters,
    kring,
    projection_from_world_row,
)
from plugins.world_semantic_plugin.stores import get_world, query_objects_near


@mcp.tool(
    title="plan_placement",
    tags={"world_semantic_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def plan_placement(
    world_id: str,
    task: str,
    anchor_name: str = "",
    anchor_hex: str = "",
    k: int = 3,
    token_budget: int = 600,
    max_candidates: int = 5,
) -> dict:
    """Suggest candidate hexes for a placement task using encoded context.

    Each candidate includes ``center_pos`` ``[x, z]`` (world meters) and
    ``neighbor_hexes`` (grid_disk k=1, excluding self) so agents can spawn
    via unity-mcp ``world_bridge`` without a separate hex→position conversion.

    Does not modify the scene. Agent should execute spawn via unity-mcp
    ``world_bridge`` then re-query for verification.
    """
    if not world_id or not task:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": [f for f in ("world_id", "task") if not locals().get(f) or not str(locals().get(f)).strip()],
        }

    wid = world_id.strip()
    ctx = await encode(
        task,
        wid,
        anchor_hex=anchor_hex.strip() or None,
        anchor_name=anchor_name.strip() or None,
        k=max(0, int(k)),
        token_budget=max(100, int(token_budget)),
        use_embeddings=True,
        refresh_dirty=True,
    )
    if ctx.get("status") != "ok":
        return ctx

    world = await get_world(wid)
    proj = projection_from_world_row(world)

    # Rank blocks already scored; map to simple placement reasons.
    candidates = []
    for b in (ctx.get("blocks") or [])[: max(1, int(max_candidates))]:
        hex_id = b["hex_id"]
        cx, cz = cell_center_meters(hex_id, proj)
        neighbors = [h for h in kring(hex_id, 1) if h != hex_id]
        candidates.append(
            {
                "hex_id": hex_id,
                "ring": b["ring"],
                "score": b["score"],
                "reason": f"ring={b['ring']} score={b['score']:.2f} matches task relevance",
                "center_pos": [cx, cz],
                "neighbor_hexes": neighbors,
            }
        )

    # Prefer emptier hexes for placement when task looks like spawn
    near = await query_objects_near(
        wid,
        anchor_hex=ctx.get("anchor_hex"),
        k=max(0, int(k)),
        limit=200,
    )
    occupancy: dict[str, int] = {}
    if near.get("status") == "ok":
        for o in near.get("objects") or []:
            hid = o.get("hex_id")
            if hid:
                occupancy[hid] = occupancy.get(hid, 0) + 1

    for c in candidates:
        c["object_count"] = occupancy.get(c["hex_id"], 0)
        if "defensible" in task.lower() and c["object_count"] == 0:
            c["score"] += 0.5
            c["reason"] += "; open ground (low occupancy)"

    candidates.sort(key=lambda c: (-c["score"], c["ring"], c["hex_id"]))
    return {
        "status": "ok",
        "world_id": wid,
        "task": task,
        "anchor_hex": ctx.get("anchor_hex"),
        "candidates": candidates[: max(1, int(max_candidates))],
        "context_preview": (ctx.get("grammar") or "")[:500],
        "execute_via": "unity-mcp world_bridge spawn/move/destroy/carve_terrain",
    }
