"""Deterministic context encoder (step 2+) with optional embedding rank (step 3)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from plugins.world_semantic_plugin.encoder.budget import BudgetedBlock, trim_to_budget
from plugins.world_semantic_plugin.hexmath import kring
from plugins.world_semantic_plugin.stores import get_world, query_objects_near
from plugins.world_semantic_plugin.stores.world_store import get_hex

logger = logging.getLogger("whiskers.plugins.world_semantic.encoder")

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_CACHE: dict[str, str] = {}


def _load_template(res: int) -> str:
    """Pick nearest resN.txt template (prefer exact, else lower, else res9)."""
    key = f"res{res}"
    if key in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[key]

    path = _TEMPLATE_DIR / f"res{res}.txt"
    if not path.is_file():
        # fall back to closest available
        available = sorted(_TEMPLATE_DIR.glob("res*.txt"))
        chosen = None
        for p in available:
            try:
                n = int(p.stem.replace("res", ""))
            except ValueError:
                continue
            if n <= res:
                chosen = p
        if chosen is None and available:
            chosen = available[-1]
        path = chosen if chosen else (_TEMPLATE_DIR / "res9.txt")

    text = path.read_text(encoding="utf-8") if path and path.is_file() else (
        "HEX {hex_id} objects={object_count}\n{summary}\n{object_lines}\n"
    )
    # strip comment lines
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    rendered = "\n".join(lines).strip() + "\n"
    _TEMPLATE_CACHE[key] = rendered
    return rendered


def _ring_of(hex_id: str, anchor: str, max_k: int) -> int:
    """Smallest k such that hex_id ∈ grid_disk(anchor, k)."""
    if hex_id == anchor:
        return 0
    prev: set[str] = set()
    for r in range(0, max_k + 1):
        disk = set(kring(anchor, r))
        if hex_id in disk and hex_id not in prev:
            return r
        prev = disk
    return max_k


def _format_objects(objects: list[dict]) -> tuple[str, str]:
    names = []
    lines = []
    for o in objects:
        name = o.get("name") or o.get("guid") or "?"
        names.append(str(name))
        pos = o.get("pos") or []
        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
            lines.append(f"  - {name} @ ({float(pos[0]):.1f},{float(pos[1]):.1f},{float(pos[2]):.1f})")
        else:
            lines.append(f"  - {name}")
    return ", ".join(names) if names else "(none)", "\n".join(lines) if lines else "  (none)"


def _render_hex(
    hex_row: dict,
    objects: list[dict],
    ring: int,
) -> str:
    res = int(hex_row.get("res") or 9)
    tpl = _load_template(res)
    names, lines = _format_objects(objects)
    tags = hex_row.get("tags") or {}
    summary = (hex_row.get("summary") or "").strip()
    if not summary and objects:
        summary = f"{len(objects)} object(s): {names}"
    elif not summary:
        summary = "(empty)"
    return tpl.format(
        hex_id=hex_row.get("hex_id", ""),
        res=res,
        ring=ring,
        object_count=hex_row.get("object_count") if hex_row.get("object_count") is not None else len(objects),
        biome=hex_row.get("biome") or "-",
        elevation_band=hex_row.get("elevation_band") if hex_row.get("elevation_band") is not None else "-",
        summary=summary,
        object_names=names,
        object_lines=lines,
        tags=tags,
    )


async def _expand_via_edges(world_id: str, seeds: set[str], depth: int = 2) -> set[str]:
    if depth <= 0 or not seeds:
        return set(seeds)
    from sqlalchemy import text
    from db_layer.connection import get_async_session

    frontier = set(seeds)
    all_hex = set(seeds)
    async with get_async_session() as session:
        for _ in range(depth):
            if not frontier:
                break
            result = await session.execute(
                text(
                    """
                    SELECT dst_hex AS h FROM edges
                    WHERE world_id = :w AND src_hex = ANY(:seeds)
                    UNION
                    SELECT src_hex AS h FROM edges
                    WHERE world_id = :w AND dst_hex = ANY(:seeds)
                    """
                ),
                {"w": world_id, "seeds": list(frontier)},
            )
            nxt = {row[0] for row in result.fetchall() if row[0]}
            nxt -= all_hex
            all_hex |= nxt
            frontier = nxt
    return all_hex


async def _fetch_hex_bundle(
    world_id: str,
    hex_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """hex_id -> {hex, objects}."""
    from sqlalchemy import text
    from db_layer.connection import get_async_session

    out: dict[str, dict[str, Any]] = {h: {"hex": {"hex_id": h, "res": 9, "object_count": 0}, "objects": []} for h in hex_ids}
    if not hex_ids:
        return out
    async with get_async_session() as session:
        hres = await session.execute(
            text(
                """
                SELECT world_id, hex_id, res, parent_hex, biome, elevation_band,
                       tags, object_count, summary, dirty, embedding IS NOT NULL AS has_embedding
                FROM hexes
                WHERE world_id = :w AND hex_id = ANY(:ids)
                """
            ),
            {"w": world_id, "ids": hex_ids},
        )
        for row in hres.mappings().all():
            d = dict(row)
            out[d["hex_id"]]["hex"] = d

        ores = await session.execute(
            text(
                """
                SELECT guid::text, hex_id, name, prefab_path, pos, tags
                FROM objects
                WHERE world_id = :w AND hex_id = ANY(:ids)
                ORDER BY name NULLS LAST
                """
            ),
            {"w": world_id, "ids": hex_ids},
        )
        for row in ores.mappings().all():
            d = dict(row)
            hid = d["hex_id"]
            if hid in out:
                out[hid]["objects"].append(d)
    return out


async def _maybe_refresh_dirty(
    world_id: str,
    hex_ids: list[str],
    *,
    cap: int = 8,
    use_embeddings: bool = True,
) -> int:
    """Step 3: re-summarize dirty hexes (bounded)."""
    from plugins.world_semantic_plugin.summarizer import refresh_hex_summary

    refreshed = 0
    for hid in hex_ids:
        if refreshed >= cap:
            break
        info = await get_hex(world_id, hid)
        if info.get("status") != "ok":
            continue
        hx = info["hex"]
        if not hx.get("dirty"):
            continue
        ok = await refresh_hex_summary(world_id, hid, use_embeddings=use_embeddings)
        if ok:
            refreshed += 1
    return refreshed


async def _embedding_scores(
    task: str,
    world_id: str,
    hex_ids: list[str],
) -> dict[str, float]:
    """Rank hexes via unity_world_vectors RAG (object + hex docs), not hexes.embedding."""
    if not task.strip() or not hex_ids:
        return {h: 0.0 for h in hex_ids}
    try:
        from plugins.world_semantic_plugin.stores.unity_world_vectors_store import hex_similarity_scores

        return await hex_similarity_scores(
            task,
            world_id,
            hex_ids,
            top_k=max(50, len(hex_ids) * 2),
        )
    except Exception as exc:
        logger.debug("unity_world_vectors rank unavailable: %s", exc)
        return {h: 0.0 for h in hex_ids}


async def encode(
    task: str,
    world_id: str,
    *,
    anchor_hex: str | None = None,
    anchor_name: str | None = None,
    resolution: int | None = None,
    token_budget: int = 800,
    k: int = 2,
    direction: str = "",
    use_embeddings: bool = True,
    refresh_dirty: bool = True,
) -> dict[str, Any]:
    """encode(task, anchor, resolution, token_budget) → spatial grammar tokens.

    Stages:
      1. candidates = kRing(anchor, k) ∩ presence of objects/hex rows
      2. expand via edges (depth ≤ 2)
      3. optional embedding rank (step 3)
      4. summary level by distance ring via templates
      5. trim to token_budget
    """
    world = await get_world(world_id)
    if not world:
        return {"status": "error", "error": "world_not_found", "world_id": world_id}

    # Resolve anchor via existing query helper
    near = await query_objects_near(
        world_id,
        anchor_hex=anchor_hex,
        anchor_name=anchor_name,
        k=k,
        direction=direction or None,
        limit=500,
    )
    if near.get("status") != "ok":
        return near

    anchor = near["anchor_hex"]
    candidate_hexes = set(kring(anchor, k))
    # Include hexes that actually appear in query results
    for o in near.get("objects") or []:
        if o.get("hex_id"):
            candidate_hexes.add(o["hex_id"])

    expanded = await _expand_via_edges(world_id, candidate_hexes, depth=2)
    hex_list = sorted(expanded)

    refreshed = 0
    if refresh_dirty:
        refreshed = await _maybe_refresh_dirty(
            world_id, hex_list, cap=8, use_embeddings=use_embeddings
        )

    bundle = await _fetch_hex_bundle(world_id, hex_list)
    emb_scores = (
        await _embedding_scores(task, world_id, hex_list) if use_embeddings else {h: 0.0 for h in hex_list}
    )

    blocks: list[BudgetedBlock] = []
    for hid in hex_list:
        b = bundle.get(hid) or {"hex": {"hex_id": hid}, "objects": []}
        # skip totally empty hexes with no row and no objects
        objects = b["objects"]
        hex_row = b["hex"]
        if not objects and hex_row.get("object_count", 0) == 0 and not hex_row.get("summary"):
            # still include ring-0 anchor
            if hid != anchor:
                continue
        ring = _ring_of(hid, anchor, k)
        # Prefer finer templates near anchor, coarser far: override res for template pick
        if resolution is not None:
            hex_row = {**hex_row, "res": resolution}
        elif ring >= 2:
            hex_row = {**hex_row, "res": min(int(hex_row.get("res") or 9), 5)}
        elif ring >= 3:
            hex_row = {**hex_row, "res": 0}

        text = _render_hex(hex_row, objects, ring)
        # score: embedding sim * 10 + proximity bonus + object presence
        score = emb_scores.get(hid, 0.0) * 10.0 + (k - ring) * 2.0 + min(len(objects), 5) * 0.1
        # light task keyword boost (deterministic step-2 path)
        task_l = (task or "").lower()
        if task_l:
            blob = text.lower()
            for word in task_l.split():
                if len(word) > 2 and word in blob:
                    score += 0.5
        blocks.append(BudgetedBlock(text=text, score=score, hex_id=hid, ring=ring))

    grammar, kept = trim_to_budget(blocks, token_budget)
    return {
        "status": "ok",
        "world_id": world_id,
        "anchor_hex": anchor,
        "task": task,
        "token_budget": token_budget,
        "hexes_considered": len(hex_list),
        "hexes_kept": len(kept),
        "dirty_refreshed": refreshed,
        "grammar": grammar,
        "blocks": [
            {"hex_id": b.hex_id, "ring": b.ring, "score": b.score, "tokens": b.tokens}
            for b in kept
        ],
    }
