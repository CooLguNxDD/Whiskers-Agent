"""Critic agent tools (step 5) — constraint checks on a region."""

from __future__ import annotations

import math
from collections import defaultdict

from core.context import mcp
from plugins.world_semantic_plugin.stores import query_objects_near


@mcp.tool(
    title="critique_region",
    tags={"world_semantic_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def critique_region(
    world_id: str,
    anchor_name: str = "",
    anchor_hex: str = "",
    k: int = 3,
    min_camp_separation: float = 25.0,
) -> dict:
    """Check soft constraints on the region around an anchor.

    Checks:
      - camps not clustered (names containing 'camp' closer than min_camp_separation)
      - path connectivity placeholder via edges (warn if no edges at all)
    """
    if not world_id:
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["world_id"]}

    near = await query_objects_near(
        world_id.strip(),
        anchor_hex=anchor_hex.strip() or None,
        anchor_name=anchor_name.strip() or None,
        k=max(0, int(k)),
        limit=300,
    )
    if near.get("status") != "ok":
        return near

    objects = near.get("objects") or []
    violations: list[dict] = []

    camps = []
    for o in objects:
        name = (o.get("name") or "").lower()
        if "camp" in name:
            pos = o.get("pos") or [0, 0, 0]
            camps.append((o, pos))

    for i in range(len(camps)):
        for j in range(i + 1, len(camps)):
            a, pa = camps[i]
            b, pb = camps[j]
            if not (isinstance(pa, (list, tuple)) and isinstance(pb, (list, tuple))):
                continue
            if len(pa) < 3 or len(pb) < 3:
                continue
            dist = math.sqrt(
                (float(pa[0]) - float(pb[0])) ** 2
                + (float(pa[2]) - float(pb[2])) ** 2
            )
            if dist < float(min_camp_separation):
                violations.append(
                    {
                        "rule": "camps_not_clustered",
                        "a": a.get("name"),
                        "b": b.get("name"),
                        "distance": round(dist, 2),
                        "min": float(min_camp_separation),
                    }
                )

    # edges presence (semantic topology)
    from sqlalchemy import text
    from db_layer.connection import get_async_session

    edge_count = 0
    try:
        async with get_async_session() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM edges WHERE world_id = :w"),
                {"w": world_id.strip()},
            )
            edge_count = int(result.scalar() or 0)
    except Exception:
        edge_count = 0

    if edge_count == 0 and len(objects) > 5:
        violations.append(
            {
                "rule": "paths_connected",
                "detail": "no semantic edges recorded; path connectivity cannot be verified",
                "severity": "warn",
            }
        )

    passed = len([v for v in violations if v.get("severity") != "warn"]) == 0
    return {
        "status": "ok",
        "pass": passed and not any(v.get("rule") == "camps_not_clustered" for v in violations),
        "world_id": world_id.strip(),
        "anchor_hex": near.get("anchor_hex"),
        "object_count": len(objects),
        "camp_count": len(camps),
        "edge_count": edge_count,
        "violations": violations,
    }
