"""Enqueue unity_world_vectors embedding jobs (route_embedding pattern).

After spatial full_index, producers insert embedding_jobs rows that
embedding_worker claims and upserts into unity_world_vectors.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from db_layer.connection import get_async_session
from db_layer.embeddings.embeddings_core import model_id_for
from db_layer.models import EmbeddingJob
from plugins.world_semantic_plugin.stores.unity_world_vectors_store import (
    DOC_KIND_HEX,
    DOC_KIND_HEX_LAYER,
    DOC_KIND_OBJECT,
    OPERATION_ID,
    PLUGIN_ID,
    delete_world_vectors,
    identity_content_hash,
    resolve_unity_world_embedding,
)
from plugins.world_semantic_plugin.hexmath import (
    Projection,
    cell3,
    layer_height_from_world_row,
    projection_from_world_row,
)
from plugins.world_semantic_plugin.stores.world_store import get_world

logger = logging.getLogger("whiskers.plugins.world_semantic")

# Cap hex docs enqueued per index batch (ancestors can fan out).
_MAX_HEX_DOCS = 64
_MAX_OBJECTS_PER_HEX_SUMMARY = 50


def _parse_guid(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _pos_array(obj: dict) -> list[float] | None:
    p = obj.get("pos") or obj.get("position")
    if isinstance(p, dict):
        return [
            float(p.get("x", 0)),
            float(p.get("y", 0)),
            float(p.get("z", 0)),
        ]
    if isinstance(p, (list, tuple)) and len(p) >= 3:
        return [float(p[0]), float(p[1]), float(p[2])]
    return None


def build_object_embed_text(
    world_id: str,
    obj: dict,
    *,
    hex_id: str,
) -> str:
    """Build a short natural-language embedding document for one world object."""
    name = obj.get("name") or "object"
    prefab = obj.get("prefab_path") or obj.get("prefabPath") or ""
    tags = obj.get("tags") or []
    if isinstance(tags, str):
        tags_s = tags
    else:
        tags_s = ", ".join(str(t) for t in tags) if tags else ""
    pos = _pos_array(obj) or [0.0, 0.0, 0.0]
    return (
        f'Object "{name}" in world {world_id}, hex {hex_id}, '
        f"pos ({pos[0]:.2f},{pos[1]:.2f},{pos[2]:.2f}). "
        f"Prefab: {prefab or 'none'}. Tags: {tags_s or 'none'}."
    )


def build_hex_embed_text(hex_id: str, objects: list[dict]) -> str:
    """Build a hex-level embedding document summarizing objects in the cell."""
    if not objects:
        return f"Empty hex {hex_id}."
    names = [str(o.get("name") or "object") for o in objects]
    head = ", ".join(names[:12])
    more = "…" if len(names) > 12 else ""
    return f"Hex {hex_id}: {len(objects)} object(s) — {head}{more}."


def build_hex_layer_embed_text(hex_id: str, layer: int, objects: list[dict]) -> str:
    """Build a y-layer embedding document for objects on one vertical slice of a hex."""
    if not objects:
        return f"Empty hex layer {hex_id}#L{layer}."
    names = [str(o.get("name") or "object") for o in objects]
    head = ", ".join(names[:12])
    more = "…" if len(names) > 12 else ""
    return (
        f"Hex layer {hex_id}#L{layer}: {len(objects)} object(s) — {head}{more}."
    )


def _object_job_row(
    world_id: str,
    obj: dict,
    *,
    hex_id: str,
    model_id: str,
) -> dict[str, Any] | None:
    guid = _parse_guid(obj.get("guid") or obj.get("id"))
    if not guid:
        return None
    pos = _pos_array(obj)
    if pos is None:
        return None
    text_body = build_object_embed_text(world_id, obj, hex_id=hex_id)
    ch = identity_content_hash(world_id, DOC_KIND_OBJECT, guid, model_id, text_body)
    tags = obj.get("tags") or []
    meta = {
        "guid": guid,
        "name": obj.get("name"),
        "hex_id": hex_id,
        "pos": pos,
        "prefab_path": obj.get("prefab_path") or obj.get("prefabPath"),
        "tags": tags,
    }
    return {
        "plugin_id": PLUGIN_ID,
        "operation_id": OPERATION_ID,
        "content_hash": ch,
        "payload": {
            "world_id": world_id,
            "doc_kind": DOC_KIND_OBJECT,
            "doc_id": guid,
            "content_text": text_body,
            "meta": meta,
        },
    }


async def _load_hex_objects(
    world_id: str,
    hex_id: str,
    *,
    y_layer: int | None = None,
) -> list[dict[str, Any]]:
    async with get_async_session() as session:
        if y_layer is None:
            result = await session.execute(
                text(
                    """
                    SELECT name, prefab_path, pos, tags, y_layer
                    FROM objects
                    WHERE world_id = :w AND hex_id = :h
                    ORDER BY name NULLS LAST
                    LIMIT :lim
                    """
                ),
                {"w": world_id, "h": hex_id, "lim": _MAX_OBJECTS_PER_HEX_SUMMARY},
            )
        else:
            result = await session.execute(
                text(
                    """
                    SELECT name, prefab_path, pos, tags, y_layer
                    FROM objects
                    WHERE world_id = :w AND hex_id = :h AND y_layer = :yl
                    ORDER BY name NULLS LAST
                    LIMIT :lim
                    """
                ),
                {
                    "w": world_id,
                    "h": hex_id,
                    "yl": int(y_layer),
                    "lim": _MAX_OBJECTS_PER_HEX_SUMMARY,
                },
            )
        return [dict(r) for r in result.mappings().all()]


async def _list_hex_layers(world_id: str, hex_id: str) -> list[int]:
    async with get_async_session() as session:
        result = await session.execute(
            text(
                """
                SELECT DISTINCT y_layer
                FROM objects
                WHERE world_id = :w AND hex_id = :h
                ORDER BY y_layer
                """
            ),
            {"w": world_id, "h": hex_id},
        )
        return [int(r[0]) for r in result.all()]


async def _hex_job_row(
    world_id: str,
    hex_id: str,
    *,
    model_id: str,
) -> dict[str, Any]:
    objects = await _load_hex_objects(world_id, hex_id)
    text_body = build_hex_embed_text(hex_id, objects)
    ch = identity_content_hash(world_id, DOC_KIND_HEX, hex_id, model_id, text_body)
    meta = {
        "hex_id": hex_id,
        "object_count": len(objects),
    }
    return {
        "plugin_id": PLUGIN_ID,
        "operation_id": OPERATION_ID,
        "content_hash": ch,
        "payload": {
            "world_id": world_id,
            "doc_kind": DOC_KIND_HEX,
            "doc_id": hex_id,
            "content_text": text_body,
            "meta": meta,
        },
    }


async def _hex_layer_job_row(
    world_id: str,
    hex_id: str,
    layer: int,
    *,
    model_id: str,
) -> dict[str, Any]:
    objects = await _load_hex_objects(world_id, hex_id, y_layer=layer)
    doc_id = f"{hex_id}#L{layer}"
    text_body = build_hex_layer_embed_text(hex_id, layer, objects)
    ch = identity_content_hash(world_id, DOC_KIND_HEX_LAYER, doc_id, model_id, text_body)
    meta = {
        "hex_id": hex_id,
        "y_layer": layer,
        "object_count": len(objects),
    }
    return {
        "plugin_id": PLUGIN_ID,
        "operation_id": OPERATION_ID,
        "content_hash": ch,
        "payload": {
            "world_id": world_id,
            "doc_kind": DOC_KIND_HEX_LAYER,
            "doc_id": doc_id,
            "content_text": text_body,
            "meta": meta,
        },
    }


async def _insert_jobs(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    async with get_async_session() as session:
        # Always re-queue on conflict. Previously only `failed` jobs were reset, so a
        # replace-index that purged unity_world_vectors left `done` jobs untouched and
        # the world never re-embedded (vectors stayed empty).
        stmt = insert(EmbeddingJob).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["plugin_id", "operation_id", "content_hash"],
            set_={
                "status": "pending",
                "payload": stmt.excluded.payload,
                "last_error": None,
                "claimed_at": None,
                "completed_at": None,
                "attempts": 0,
            },
        )
        await session.execute(stmt)
        await session.execute(text("NOTIFY embedding_jobs_new"))
        await session.commit()
    return len(rows)


async def enqueue_unity_world_embed_jobs(
    world_id: str,
    objects: list[dict],
    *,
    touched_hex_ids: list[str] | None = None,
    replace: bool = False,
    include_objects: bool = True,
    include_hexes: bool = True,
) -> dict[str, int]:
    """After full_index: purge (if replace) and enqueue object + hex embed jobs.

    Returns counts: purged, object_jobs, hex_jobs, enqueued.
    """
    if replace:
        purged = await delete_world_vectors(world_id)
    else:
        purged = 0

    try:
        sel = await resolve_unity_world_embedding()
        model_id = model_id_for(sel)
    except Exception as exc:
        logger.warning("unity embed enqueue: resolve model failed: %s", exc)
        return {
            "purged": purged,
            "object_jobs": 0,
            "hex_jobs": 0,
            "enqueued": 0,
        }

    world = await get_world(world_id)
    if not world:
        logger.warning("unity embed enqueue: world %s missing", world_id)
        return {
            "purged": purged,
            "object_jobs": 0,
            "hex_jobs": 0,
            "enqueued": 0,
        }
    proj: Projection = projection_from_world_row(world)
    lh = layer_height_from_world_row(world)

    rows: list[dict[str, Any]] = []
    object_jobs = 0
    if include_objects:
        for obj in objects or []:
            if not isinstance(obj, dict):
                continue
            pos = _pos_array(obj)
            if pos is None:
                continue
            hex_id, _yl = cell3(pos[0], pos[1], pos[2], proj=proj, layer_height=lh)
            row = _object_job_row(world_id, obj, hex_id=hex_id, model_id=model_id)
            if row:
                rows.append(row)
                object_jobs += 1

    hex_jobs = 0
    if include_hexes:
        hex_ids = list(dict.fromkeys(touched_hex_ids or []))[:_MAX_HEX_DOCS]
        for hid in hex_ids:
            row = await _hex_job_row(world_id, hid, model_id=model_id)
            rows.append(row)
            hex_jobs += 1
            # Per-layer hex docs (additive; keeps 2D hex aggregate).
            for layer in await _list_hex_layers(world_id, hid):
                rows.append(
                    await _hex_layer_job_row(
                        world_id, hid, layer, model_id=model_id
                    )
                )
                hex_jobs += 1

    # Deduplicate identical content_hash within this batch (defensive).
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in rows:
        ch = r["content_hash"]
        if ch in seen:
            continue
        seen.add(ch)
        deduped.append(r)

    enqueued = await _insert_jobs(deduped)
    logger.info(
        "unity embed enqueue: world=%s purged=%s object_jobs=%s hex_jobs=%s enqueued=%s",
        world_id,
        purged,
        object_jobs,
        hex_jobs,
        enqueued,
    )
    return {
        "purged": purged,
        "object_jobs": object_jobs,
        "hex_jobs": hex_jobs,
        "enqueued": enqueued,
    }
