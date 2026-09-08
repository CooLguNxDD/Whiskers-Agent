"""Standalone driver mirroring the /world-craft MCP loop, run in-process inside the
whiskers mcp-server container (bypasses MCP/OAuth transport which isn't reachable from
this session). Calls the exact same plugin functions the MCP tools wrap.
"""
import asyncio
import json
import sys
import datetime


def _json_default(o):
    """Serialize datetime values to ISO strings for JSON dumps."""
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    return str(o)


def dumps(obj):
    """JSON-encode *obj* with datetime-safe defaults (stdout for the driver)."""
    return json.dumps(obj, default=_json_default)

from plugins.world_semantic_plugin.MCPTools.context_tools import (
    query_context,
    get_hex_tool,
    list_objects_in_hex_tool,
)
from plugins.world_semantic_plugin.MCPTools.action_tools import plan_placement
from plugins.world_semantic_plugin.MCPTools.lease_tools import claim_hexes, release_hexes, renew_lease
from plugins.world_semantic_plugin.MCPTools.critic_tools import critique_region


async def cmd_plan(a):
    """Plan placement, claim picked hexes, and print pre-critique JSON for a craft task."""
    ctx = await query_context(
        world_id=a["world_id"], task=a["task"], anchor_name=a.get("anchor_name", ""),
        anchor_hex=a.get("anchor_hex", ""), k=a.get("k", 2), raw=False, token_budget=800,
    )
    plan = await plan_placement(
        world_id=a["world_id"], task=a["task"], anchor_name=a.get("anchor_name", ""),
        anchor_hex=a.get("anchor_hex", ""), k=a.get("k", 3), max_candidates=a.get("max_candidates", 5),
    )
    candidates = plan.get("candidates", [])
    exclude = set(a.get("exclude", []))
    picked = [c for c in candidates if c["hex_id"] not in exclude][: a.get("pick_n", 1)]
    result = {"query_context_status": ctx.get("status"), "plan_placement": plan, "picked": picked}
    if not picked:
        result["error"] = "no_candidates_after_exclude"
        print(dumps(result))
        return
    hexes = [c["hex_id"] for c in picked]
    claim = await claim_hexes(world_id=a["world_id"], hexes=hexes, agent_id=a["agent_id"], ttl_seconds=a.get("ttl", 180))
    result["claim"] = claim
    if claim.get("status") != "ok":
        print(dumps(result))
        return
    crit = await critique_region(world_id=a["world_id"], anchor_name=a.get("anchor_name", ""), anchor_hex=a.get("anchor_hex", ""), k=a.get("k", 3))
    result["critique_pre"] = crit
    print(dumps(result))


async def cmd_claim_fixed(a):
    """Claim explicit hex_ids (used for zone 1: fixed at world origin, no plan needed)."""
    claim = await claim_hexes(world_id=a["world_id"], hexes=a["hexes"], agent_id=a["agent_id"], ttl_seconds=a.get("ttl", 180))
    result = {"claim": claim}
    if claim.get("status") == "ok":
        crit = await critique_region(world_id=a["world_id"], anchor_hex=a["hexes"][0], k=a.get("k", 3))
        result["critique_pre"] = crit
    print(dumps(result))


async def cmd_confirm(a):
    """Verify placed hexes, list objects, release leases, and print final critique JSON."""
    ctx = await query_context(
        world_id=a["world_id"], task="verify placement", anchor_name=a.get("anchor_name", ""),
        anchor_hex=a.get("anchor_hex", ""), k=a.get("k", 3), raw=False, token_budget=400,
    )
    hex_details = {h: await get_hex_tool(world_id=a["world_id"], hex_id=h) for h in a["hexes"]}
    objs = {h: await list_objects_in_hex_tool(world_id=a["world_id"], hex_id=h) for h in a["hexes"]}
    release = await release_hexes(world_id=a["world_id"], hexes=a["hexes"], agent_id=a["agent_id"])
    crit = await critique_region(world_id=a["world_id"], anchor_name=a.get("anchor_name", ""), anchor_hex=a.get("anchor_hex", ""), k=a.get("k", 3))
    print(dumps({
        "query_context_status": ctx.get("status"),
        "hex_details": hex_details,
        "objects": objs,
        "release": release,
        "critique_final": crit,
    }))


async def cmd_renew(a):
    """Extend lease TTL on the given hexes for the agent and print the result."""
    r = await renew_lease(world_id=a["world_id"], hexes=a["hexes"], agent_id=a["agent_id"], ttl_seconds=a.get("ttl", 180))
    print(dumps(r))


async def cmd_ring(a):
    """Exact H3 ring at radius k around anchor_hex (k=1 -> 6 cells, k=2 ring-only -> 12 cells)."""
    from plugins.world_semantic_plugin.hexmath import kring, cell_center_meters, projection_from_world_row
    from plugins.world_semantic_plugin.stores import get_world

    world = await get_world(a["world_id"])
    proj = projection_from_world_row(world)
    k = a["k"]
    outer = set(kring(a["anchor_hex"], k))
    inner = set(kring(a["anchor_hex"], k - 1)) if k > 0 else set()
    ring_only = sorted(outer - inner)
    cells = []
    for h in ring_only:
        cx, cz = cell_center_meters(h, proj)
        cells.append({"hex_id": h, "center_pos": [cx, cz]})
    print(dumps({"k": k, "count": len(cells), "cells": cells}))


async def cmd_seed_hexes(a):
    """Pre-create empty hex rows (+ ancestor chain) so claim_hexes' UPDATE can find them.
    Needed for res-9 cells with zero indexed objects so far (full_index/apply_diff only
    insert rows for hexes an object actually touched)."""
    from db_layer.connection import get_async_session
    from plugins.world_semantic_plugin.stores.world_store import _ensure_hex_chain
    from plugins.world_semantic_plugin.hexmath import projection_from_world_row
    from plugins.world_semantic_plugin.stores import get_world

    world = await get_world(a["world_id"])
    proj = projection_from_world_row(world)
    async with get_async_session() as session:
        for h in a["hexes"]:
            await _ensure_hex_chain(session, a["world_id"], h, proj)
        await session.commit()
    print(dumps({"status": "ok", "seeded": a["hexes"]}))


async def cmd_search(a):
    """Run semantic world-vector search and print ranked hits as JSON."""
    from plugins.world_semantic_plugin.MCPTools.context_tools import search_world_vectors
    r = await search_world_vectors(world_id=a["world_id"], query=a["query"], top_k=a.get("top_k", 10), doc_kind=a.get("doc_kind", ""))
    print(dumps(r))


MODES = {"plan": cmd_plan, "claim_fixed": cmd_claim_fixed, "confirm": cmd_confirm, "renew": cmd_renew, "search": cmd_search, "ring": cmd_ring, "seed_hexes": cmd_seed_hexes}


def main():
    """CLI entry: ``python craft_driver.py <mode> '<json-args>'``."""
    mode = sys.argv[1]
    args = json.loads(sys.argv[2])
    asyncio.run(MODES[mode](args))


if __name__ == "__main__":
    main()
