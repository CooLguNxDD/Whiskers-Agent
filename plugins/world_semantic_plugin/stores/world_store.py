"""Async SQL stores for worlds / hexes / objects (step 1: raw SQL, no embeddings)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text

from db_layer.connection import get_async_session
from plugins.world_semantic_plugin.hexmath import (
    DEFAULT_LAYER_HEIGHT,
    Projection,
    ancestors,
    bearing_northish,
    cell,
    cell3,
    kring,
    layer_height_from_world_row,
    projection_from_world_row,
    y_layer,
)
from plugins.world_semantic_plugin.plugin_config import SETTINGS

logger = logging.getLogger("whiskers.plugins.world_semantic")


def _default_proj_params() -> dict[str, Any]:
    return {
        "anchor_lat": float(SETTINGS.get("default_anchor_lat", 0.0)),
        "anchor_lng": float(SETTINGS.get("default_anchor_lng", 0.0)),
        "meters_per_degree": float(SETTINGS.get("default_meters_per_degree", 111320.0)),
        "base_res": int(SETTINGS.get("default_base_res", 9)),
        "layer_height": float(SETTINGS.get("default_layer_height", DEFAULT_LAYER_HEIGHT)),
    }


async def ensure_world(
    world_id: str,
    *,
    name: str | None = None,
    anchor_lat: float | None = None,
    anchor_lng: float | None = None,
    meters_per_degree: float | None = None,
    base_res: int | None = None,
    layer_height: float | None = None,
) -> dict[str, Any]:
    """Upsert a world row; returns the stored row."""
    defaults = _default_proj_params()
    params = {
        "world_id": world_id,
        "name": name or world_id,
        "anchor_lat": defaults["anchor_lat"] if anchor_lat is None else anchor_lat,
        "anchor_lng": defaults["anchor_lng"] if anchor_lng is None else anchor_lng,
        "meters_per_degree": (
            defaults["meters_per_degree"] if meters_per_degree is None else meters_per_degree
        ),
        "base_res": defaults["base_res"] if base_res is None else base_res,
        "layer_height": defaults["layer_height"] if layer_height is None else float(layer_height),
    }
    async with get_async_session() as session:
        await session.execute(
            text(
                """
                INSERT INTO worlds (world_id, name, anchor_lat, anchor_lng,
                                    meters_per_degree, base_res, layer_height)
                VALUES (:world_id, :name, :anchor_lat, :anchor_lng,
                        :meters_per_degree, :base_res, :layer_height)
                ON CONFLICT (world_id) DO UPDATE SET
                    name = COALESCE(EXCLUDED.name, worlds.name),
                    anchor_lat = EXCLUDED.anchor_lat,
                    anchor_lng = EXCLUDED.anchor_lng,
                    meters_per_degree = EXCLUDED.meters_per_degree,
                    base_res = EXCLUDED.base_res,
                    layer_height = EXCLUDED.layer_height,
                    updated_at = now()
                """
            ),
            params,
        )
        await session.commit()
    return await get_world(world_id)  # type: ignore[return-value]


async def get_world(world_id: str) -> dict[str, Any] | None:
    """Load a world row (projection + layer_height) or None if missing."""
    async with get_async_session() as session:
        result = await session.execute(
            text(
                """
                SELECT world_id, name, anchor_lat, anchor_lng,
                       meters_per_degree, base_res, layer_height,
                       created_at, updated_at
                FROM worlds WHERE world_id = :world_id
                """
            ),
            {"world_id": world_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def _ensure_hex_chain(
    session,
    world_id: str,
    hex_id: str,
    proj: Projection,
) -> None:
    """Insert hex + ancestors if missing; mark dirty; do not touch object_count here."""
    chain = ancestors(hex_id, stop_res=0)
    for i, h in enumerate(chain):
        res = __import__("h3").get_resolution(h)
        parent_hex = chain[i + 1] if i + 1 < len(chain) else None
        # DO NOTHING on conflict to avoid lock storms when concurrent
        # batches (or stacked requests) insert the same ancestor hexes.
        # Dirty marking happens via _recompute_object_counts for touched leaves.
        await session.execute(
            text(
                """
                INSERT INTO hexes (world_id, hex_id, res, parent_hex, dirty, object_count)
                VALUES (:world_id, :hex_id, :res, :parent_hex, TRUE, 0)
                ON CONFLICT (world_id, hex_id) DO NOTHING
                """
            ),
            {
                "world_id": world_id,
                "hex_id": h,
                "res": res,
                "parent_hex": parent_hex,
            },
        )


async def _recompute_object_counts(session, world_id: str, hex_ids: set[str]) -> None:
    """Recount objects for the given hexes (and leave ancestors dirty)."""
    if not hex_ids:
        return
    await session.execute(
        text(
            """
            UPDATE hexes h SET
                object_count = (
                    SELECT COUNT(*) FROM objects o
                    WHERE o.world_id = :world_id AND o.hex_id = h.hex_id
                ),
                dirty = TRUE,
                updated_at = now()
            WHERE h.world_id = :world_id AND h.hex_id = ANY(:hex_ids)
            """
        ),
        {"world_id": world_id, "hex_ids": list(hex_ids)},
    )


def _parse_guid(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, TypeError, AttributeError):
        # Unity GlobalObjectId is not always a UUID — hash deterministically to UUID5.
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"unity-goid:{raw}"))


def _pos_array(obj: dict) -> list[float] | None:
    pos = obj.get("pos") or obj.get("position")
    if pos is None:
        return None
    if isinstance(pos, dict):
        return [float(pos.get("x", 0)), float(pos.get("y", 0)), float(pos.get("z", 0))]
    if isinstance(pos, (list, tuple)) and len(pos) >= 3:
        return [float(pos[0]), float(pos[1]), float(pos[2])]
    return None


def _bounds_array(obj: dict) -> list[float] | None:
    b = obj.get("bounds")
    if not b:
        return None
    if isinstance(b, dict):
        c = b.get("center") or {}
        e = b.get("extents") or b.get("size") or {}
        return [
            float(c.get("x", 0)),
            float(c.get("y", 0)),
            float(c.get("z", 0)),
            float(e.get("x", 0)),
            float(e.get("y", 0)),
            float(e.get("z", 0)),
        ]
    if isinstance(b, (list, tuple)) and len(b) >= 6:
        return [float(x) for x in b[:6]]
    return None


async def _upsert_object(
    session,
    world_id: str,
    obj: dict,
    proj: Projection,
    touched_hexes: set[str],
    *,
    layer_height: float = DEFAULT_LAYER_HEIGHT,
) -> str | None:
    guid_raw = obj.get("guid") or obj.get("id")
    guid = _parse_guid(str(guid_raw) if guid_raw is not None else None)
    if not guid:
        return None
    pos = _pos_array(obj)
    if pos is None:
        return None
    hex_id, yl = cell3(pos[0], pos[1], pos[2], proj=proj, layer_height=layer_height)
    await _ensure_hex_chain(session, world_id, hex_id, proj)
    touched_hexes.add(hex_id)
    for a in ancestors(hex_id):
        touched_hexes.add(a)

    components = obj.get("components") or []
    tags = obj.get("tags") or []
    state = obj.get("state") or {}
    bounds = _bounds_array(obj)

    await session.execute(
        text(
            """
            INSERT INTO objects (
                world_id, guid, hex_id, y_layer, name, prefab_path,
                components, state, pos, bounds, tags
            ) VALUES (
                :world_id, CAST(:guid AS uuid), :hex_id, :y_layer, :name, :prefab_path,
                CAST(:components AS jsonb), CAST(:state AS jsonb),
                :pos, :bounds, CAST(:tags AS jsonb)
            )
            ON CONFLICT (world_id, guid) DO UPDATE SET
                hex_id = EXCLUDED.hex_id,
                y_layer = EXCLUDED.y_layer,
                name = EXCLUDED.name,
                prefab_path = EXCLUDED.prefab_path,
                components = EXCLUDED.components,
                state = EXCLUDED.state,
                pos = EXCLUDED.pos,
                bounds = EXCLUDED.bounds,
                tags = EXCLUDED.tags,
                updated_at = now()
            """
        ),
        {
            "world_id": world_id,
            "guid": guid,
            "hex_id": hex_id,
            "y_layer": yl,
            "name": obj.get("name"),
            "prefab_path": obj.get("prefab_path") or obj.get("prefabPath"),
            "components": json.dumps(components),
            "state": json.dumps(state),
            "pos": pos,
            "bounds": bounds,
            "tags": json.dumps(tags),
        },
    )
    return hex_id


async def full_index(
    world_id: str,
    objects: list[dict],
    *,
    replace: bool = True,
    world_meta: dict | None = None,
) -> dict[str, Any]:
    """Full scene index. If replace=True, wipe existing objects for the world first."""
    meta = world_meta or {}
    world = await ensure_world(
        world_id,
        name=meta.get("name"),
        anchor_lat=meta.get("anchor_lat"),
        anchor_lng=meta.get("anchor_lng"),
        meters_per_degree=meta.get("meters_per_degree"),
        base_res=meta.get("base_res"),
        layer_height=meta.get("layer_height"),
    )
    proj = projection_from_world_row(world)
    lh = layer_height_from_world_row(world)
    touched: set[str] = set()
    upserted = 0

    async with get_async_session() as session:
        if replace:
            await session.execute(
                text("DELETE FROM objects WHERE world_id = :world_id"),
                {"world_id": world_id},
            )
            await session.execute(
                text(
                    """
                    UPDATE hexes SET object_count = 0, dirty = TRUE, updated_at = now()
                    WHERE world_id = :world_id
                    """
                ),
                {"world_id": world_id},
            )

        for obj in objects:
            if await _upsert_object(session, world_id, obj, proj, touched, layer_height=lh):
                upserted += 1

        await _recompute_object_counts(session, world_id, touched)
        await session.commit()

    return {
        "status": "ok",
        "world_id": world_id,
        "objects_upserted": upserted,
        "hexes_touched": len(touched),
        "touched_hex_ids": sorted(touched),
        "base_res": proj.base_res,
        "layer_height": lh,
    }


async def apply_diff(world_id: str, ops: list[dict]) -> dict[str, Any]:
    """Apply batched create/update/delete ops from Unity ChangeWatcher."""
    world = await get_world(world_id)
    if not world:
        world = await ensure_world(world_id)
    proj = projection_from_world_row(world)
    lh = layer_height_from_world_row(world)
    touched: set[str] = set()
    created = updated = deleted = 0

    async with get_async_session() as session:
        for op in ops:
            kind = (op.get("op") or op.get("operation") or "upsert").lower()
            if kind in ("delete", "destroy", "remove"):
                guid = _parse_guid(str(op.get("guid") or op.get("id") or ""))
                if not guid:
                    continue
                # Capture old hex for recount. FOR UPDATE serializes against a
                # concurrent apply_diff on the same guid so this read can't
                # interleave with another transaction's write to the same row
                # (which would otherwise leave a hex out of the touched set).
                old = await session.execute(
                    text(
                        "SELECT hex_id FROM objects WHERE world_id = :w AND guid = CAST(:g AS uuid) FOR UPDATE"
                    ),
                    {"w": world_id, "g": guid},
                )
                row = old.first()
                if row:
                    touched.add(row[0])
                    for a in ancestors(row[0]):
                        touched.add(a)
                await session.execute(
                    text(
                        "DELETE FROM objects WHERE world_id = :w AND guid = CAST(:g AS uuid)"
                    ),
                    {"w": world_id, "g": guid},
                )
                deleted += 1
            else:
                # upsert / create / move / update
                before_hex = None
                guid = _parse_guid(str(op.get("guid") or op.get("id") or ""))
                if guid:
                    # Same FOR UPDATE rationale as the delete branch above.
                    old = await session.execute(
                        text(
                            "SELECT hex_id FROM objects WHERE world_id = :w AND guid = CAST(:g AS uuid) FOR UPDATE"
                        ),
                        {"w": world_id, "g": guid},
                    )
                    r = old.first()
                    if r:
                        before_hex = r[0]
                        updated += 1
                    else:
                        created += 1
                hex_id = await _upsert_object(
                    session, world_id, op, proj, touched, layer_height=lh
                )
                if before_hex and hex_id and before_hex != hex_id:
                    touched.add(before_hex)
                    for a in ancestors(before_hex):
                        touched.add(a)

        await _recompute_object_counts(session, world_id, touched)
        await session.commit()

    return {
        "status": "ok",
        "world_id": world_id,
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "hexes_touched": len(touched),
    }


async def list_objects_in_hex(
    world_id: str,
    hex_id: str,
    *,
    include_children: bool = False,
    y_layer: int | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """List objects in a hex; optional *y_layer* filters a single vertical slice."""
    async with get_async_session() as session:
        if include_children:
            # Objects whose hex is the cell or any descendant — step 1: exact hex only + k=0.
            # Full hierarchy query deferred; use exact match for now.
            pass
        if y_layer is None:
            result = await session.execute(
                text(
                    """
                    SELECT guid::text, hex_id, y_layer, name, prefab_path, components, state,
                           pos, bounds, tags, updated_at
                    FROM objects
                    WHERE world_id = :world_id AND hex_id = :hex_id
                    ORDER BY name NULLS LAST
                    LIMIT :limit
                    """
                ),
                {"world_id": world_id, "hex_id": hex_id, "limit": limit},
            )
        else:
            result = await session.execute(
                text(
                    """
                    SELECT guid::text, hex_id, y_layer, name, prefab_path, components, state,
                           pos, bounds, tags, updated_at
                    FROM objects
                    WHERE world_id = :world_id AND hex_id = :hex_id AND y_layer = :y_layer
                    ORDER BY name NULLS LAST
                    LIMIT :limit
                    """
                ),
                {
                    "world_id": world_id,
                    "hex_id": hex_id,
                    "y_layer": int(y_layer),
                    "limit": limit,
                },
            )
        rows = [dict(r) for r in result.mappings().all()]
    return {
        "status": "ok",
        "world_id": world_id,
        "hex_id": hex_id,
        "y_layer": y_layer,
        "count": len(rows),
        "objects": rows,
    }


async def get_hex(world_id: str, hex_id: str) -> dict[str, Any]:
    """Fetch a hex row plus per-y_layer object counts (``layer_counts``)."""
    async with get_async_session() as session:
        result = await session.execute(
            text(
                """
                SELECT world_id, hex_id, res, parent_hex, biome, elevation_band,
                       tags, object_count, summary, dirty, lease_holder, lease_expiry, updated_at
                FROM hexes
                WHERE world_id = :world_id AND hex_id = :hex_id
                """
            ),
            {"world_id": world_id, "hex_id": hex_id},
        )
        row = result.mappings().first()
        if not row:
            return {"status": "error", "error": "hex_not_found", "world_id": world_id, "hex_id": hex_id}
        hex_row = dict(row)
        layers = await session.execute(
            text(
                """
                SELECT y_layer, COUNT(*)::int AS object_count
                FROM objects
                WHERE world_id = :world_id AND hex_id = :hex_id
                GROUP BY y_layer
                ORDER BY y_layer
                """
            ),
            {"world_id": world_id, "hex_id": hex_id},
        )
        layer_counts = [
            {"y_layer": r["y_layer"], "object_count": r["object_count"]}
            for r in layers.mappings().all()
        ]
        hex_row["layer_counts"] = layer_counts
        return {"status": "ok", "hex": hex_row}


async def query_objects_near(
    world_id: str,
    *,
    anchor_hex: str | None = None,
    anchor_name: str | None = None,
    k: int = 2,
    name_contains: str | None = None,
    direction: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Step-1 raw spatial query: kRing + optional name/direction filters."""
    world = await get_world(world_id)
    if not world:
        return {"status": "error", "error": "world_not_found", "world_id": world_id}
    proj = projection_from_world_row(world)

    if not anchor_hex and anchor_name:
        async with get_async_session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT hex_id, name, pos FROM objects
                    WHERE world_id = :world_id AND name ILIKE :pat
                    ORDER BY updated_at DESC LIMIT 1
                    """
                ),
                {"world_id": world_id, "pat": f"%{anchor_name}%"},
            )
            row = result.mappings().first()
            if not row:
                return {
                    "status": "error",
                    "error": "anchor_not_found",
                    "anchor_name": anchor_name,
                }
            anchor_hex = row["hex_id"]
            anchor_resolved = dict(row)
    else:
        anchor_resolved = {"hex_id": anchor_hex}

    if not anchor_hex:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["anchor_hex|anchor_name"],
        }

    candidates = set(kring(anchor_hex, k))
    async with get_async_session() as session:
        result = await session.execute(
            text(
                """
                SELECT guid::text, hex_id, name, prefab_path, components, state,
                       pos, bounds, tags, updated_at
                FROM objects
                WHERE world_id = :world_id AND hex_id = ANY(:hexes)
                ORDER BY name NULLS LAST
                LIMIT :limit
                """
            ),
            {"world_id": world_id, "hexes": list(candidates), "limit": max(limit * 4, limit)},
        )
        rows = [dict(r) for r in result.mappings().all()]

    if name_contains:
        pat = name_contains.lower()
        rows = [r for r in rows if r.get("name") and pat in str(r["name"]).lower()]

    direction = (direction or "").lower().strip()
    if direction in ("north", "n", "south", "s", "east", "e", "west", "w"):
        filtered = []
        for r in rows:
            h = r.get("hex_id")
            if not h or h == anchor_hex:
                # exclude anchor itself for directional queries unless no other filter
                if direction:
                    continue
            b = bearing_northish(anchor_hex, h, proj)
            ok = False
            if direction in ("north", "n"):
                ok = b <= 45 or b >= 315
            elif direction in ("south", "s"):
                ok = 135 <= b <= 225
            elif direction in ("east", "e"):
                ok = 45 < b < 135
            elif direction in ("west", "w"):
                ok = 225 < b < 315
            if ok:
                filtered.append(r)
        rows = filtered

    rows = rows[:limit]
    return {
        "status": "ok",
        "world_id": world_id,
        "anchor_hex": anchor_hex,
        "anchor": anchor_resolved,
        "k": k,
        "candidate_hexes": len(candidates),
        "count": len(rows),
        "objects": rows,
    }


async def describe_region(
    world_id: str,
    *,
    anchor_name: str | None = None,
    anchor_hex: str | None = None,
    k: int = 2,
    direction: str | None = None,
) -> dict[str, Any]:
    """Human-readable region description for step-1 milestone queries."""
    q = await query_objects_near(
        world_id,
        anchor_hex=anchor_hex,
        anchor_name=anchor_name,
        k=k,
        direction=direction,
        limit=50,
    )
    if q.get("status") != "ok":
        return q

    objects = q.get("objects") or []
    lines = []
    anchor_label = anchor_name or q.get("anchor_hex")
    dir_label = f" to the {direction}" if direction else " nearby"
    lines.append(f"Region{dir_label} of '{anchor_label}' (k={k}): {len(objects)} object(s).")
    for o in objects:
        pos = o.get("pos") or []
        pos_s = ""
        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
            pos_s = f" @ ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})"
        lines.append(f"- {o.get('name') or o.get('guid')} hex={o.get('hex_id')}{pos_s}")

    return {
        "status": "ok",
        "world_id": world_id,
        "anchor_hex": q.get("anchor_hex"),
        "count": len(objects),
        "description": "\n".join(lines),
        "objects": objects,
    }
