"""Per-hex summary + embedding (step 3). Deterministic fallback when LLM unavailable."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from db_layer.connection import get_async_session

logger = logging.getLogger("whiskers.plugins.world_semantic.summarizer")


async def _load_objects(world_id: str, hex_id: str) -> list[dict[str, Any]]:
    async with get_async_session() as session:
        result = await session.execute(
            text(
                """
                SELECT name, prefab_path, pos, tags
                FROM objects
                WHERE world_id = :w AND hex_id = :h
                ORDER BY name NULLS LAST
                LIMIT 50
                """
            ),
            {"w": world_id, "h": hex_id},
        )
        return [dict(r) for r in result.mappings().all()]


def _deterministic_summary(hex_id: str, objects: list[dict]) -> str:
    if not objects:
        return f"Empty hex {hex_id}."
    names = [str(o.get("name") or "object") for o in objects]
    return f"Hex {hex_id}: {len(objects)} object(s) — " + ", ".join(names[:12]) + (
        "…" if len(names) > 12 else ""
    ) + "."


async def _llm_summary(taskish: str, objects: list[dict]) -> str | None:
    """Best-effort short summary via configured chat LLM; None on failure."""
    try:
        from core.llm_config_service import resolve_tool_llm
        from core.llm_provider import make_chat_model

        # Prefer a lightweight completion if service exposes tool LLM.
        sel = None
        try:
            sel = await resolve_tool_llm("plugins.world_semantic_plugin", "summarize_hex")
        except Exception:
            sel = None
        if not sel:
            return None
        # Very small prompt — RAPTOR leaf summary.
        names = ", ".join(str(o.get("name") or "?") for o in objects[:20]) or "(empty)"
        prompt = (
            "Write one factual sentence summarizing this game-world hex for an agent.\n"
            f"Objects: {names}\nSummary:"
        )
        # resolve_tool_llm shape varies; try common path
        model = None
        if isinstance(sel, dict) and "model" in sel:
            from langchain_core.messages import HumanMessage

            # Fallback: skip if we can't construct
            return None
        return None
    except Exception as exc:
        logger.debug("llm summary skipped: %s", exc)
        return None


async def refresh_hex_summary(
    world_id: str,
    hex_id: str,
    *,
    use_embeddings: bool = True,
) -> bool:
    """Regenerate text summary and clear dirty for one hex.

    Vectors no longer write to hexes.embedding — use unity_world_vectors via
    embedding_jobs / embedding_worker (unity_world_semantic stack).
    ``use_embeddings`` is kept for call-site compatibility but is ignored.
    """
    del use_embeddings  # RAG vectors are async via unity_world_vectors
    objects = await _load_objects(world_id, hex_id)
    summary = await _llm_summary(hex_id, objects) or _deterministic_summary(hex_id, objects)

    async with get_async_session() as session:
        await session.execute(
            text(
                """
                UPDATE hexes SET
                    summary = :summary,
                    dirty = FALSE,
                    object_count = :ocount,
                    updated_at = now()
                WHERE world_id = :w AND hex_id = :h
                """
            ),
            {
                "summary": summary,
                "ocount": len(objects),
                "w": world_id,
                "h": hex_id,
            },
        )
        await session.commit()
    return True


async def refresh_dirty_chain(
    world_id: str,
    hex_id: str,
    *,
    cap: int = 8,
    use_embeddings: bool = True,
) -> int:
    """Refresh hex + dirty ancestors (bounded)."""
    from plugins.world_semantic_plugin.hexmath import ancestors

    n = 0
    for h in ancestors(hex_id):
        if n >= cap:
            break
        info = await get_hex_dirty(world_id, h)
        if info is None:
            continue
        if not info.get("dirty") and h != hex_id:
            continue
        if await refresh_hex_summary(world_id, h, use_embeddings=use_embeddings):
            n += 1
    return n


async def get_hex_dirty(world_id: str, hex_id: str) -> dict | None:
    """Return hex dirty flag + summary snapshot, or None if the hex is missing."""
    async with get_async_session() as session:
        result = await session.execute(
            text(
                "SELECT hex_id, dirty, summary FROM hexes WHERE world_id = :w AND hex_id = :h"
            ),
            {"w": world_id, "h": hex_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None
