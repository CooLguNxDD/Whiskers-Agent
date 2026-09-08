"""Harness engine: static core instructions + RAG recipes/anti-patterns."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("whiskers.harness")

_INSTRUCTIONS_DIR = Path(__file__).resolve().parent / "instructions"
_STATIC_CACHE: str | None = None


@dataclass
class HarnessContext:
    """Retrieved harness material for one planner turn."""

    static_instructions: str = ""
    recipes: list[dict] = field(default_factory=list)
    anti_patterns: list[dict] = field(default_factory=list)
    best_recipe: dict | None = None
    block: str = ""
    enabled: bool = True


def _harness_cfg() -> dict:
    try:
        from utils.server_config import HARNESS_CONFIG

        return dict(HARNESS_CONFIG or {})
    except Exception:
        return {
            "enabled": True,
            "recipe_top_k": 3,
            "anti_top_k": 3,
            "similarity_threshold": 0.78,
            "max_block_chars": 3500,
            "include_user_memory": False,
            "write_recipes": True,
            "write_anti_patterns": True,
        }


def load_static_instructions(*, force_reload: bool = False) -> str:
    """Load core instruction text from disk seed (sync fallback).

    Prefer ``load_instructions_async`` / DB JSON via
    ``core.memory.harness_instructions`` in async planner paths.
    """
    global _STATIC_CACHE
    if _STATIC_CACHE is not None and not force_reload:
        return _STATIC_CACHE

    # Prefer core seed path; fall back to legacy graph-local copies
    seed_dirs = [
        Path(__file__).resolve().parents[2] / "core" / "memory" / "seed_instructions",
        _INSTRUCTIONS_DIR,
    ]
    parts: list[str] = []
    for d in seed_dirs:
        if not d.is_dir():
            continue
        files = sorted(
            d.glob("*.md"),
            key=lambda p: (0 if p.name.startswith("collection") else 1, p.name),
        )
        for path in files:
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning("harness: cannot read %s: %s", path, exc)
                continue
            if text:
                parts.append(text)
        if parts:
            break
    _STATIC_CACHE = "\n\n".join(parts)
    return _STATIC_CACHE


async def load_instructions_async() -> str:
    """Load global harness instructions from DB JSON (seeded on first use)."""
    try:
        from core.memory import format_instructions_text_async

        text = await format_instructions_text_async(ensure_seeded=True)
        if text and text.strip():
            return text.strip()
    except Exception as exc:
        logger.warning("harness: DB instructions failed, using disk seed: %s", exc)
    return load_static_instructions(force_reload=False)


def format_harness_block(
    *,
    static_instructions: str = "",
    recipes: list[dict] | None = None,
    anti_patterns: list[dict] | None = None,
    max_chars: int = 3500,
) -> str:
    """Build the planner-facing harness section (char-capped)."""
    sections: list[str] = []

    if static_instructions and static_instructions.strip():
        sections.append("## Core instructions\n" + static_instructions.strip())

    if recipes:
        lines = ["## Learned recipes (similar past successes)"]
        for r in recipes:
            ops = r.get("op_ids") or []
            chain = " → ".join(ops) if ops else "(see content)"
            sim = r.get("similarity")
            sim_s = f" sim={sim:.2f}" if isinstance(sim, (int, float)) else ""
            body = (r.get("content") or "").split("\n")[0][:200]
            lines.append(f"-{sim_s} {chain}: {body}")
        sections.append("\n".join(lines))

    if anti_patterns:
        lines = ["## Avoid (past failures)"]
        for a in anti_patterns:
            ops = a.get("plan_ops") or []
            ops_s = " → ".join(ops) if ops else "?"
            detail = (a.get("detail") or a.get("content") or "")[:180]
            lines.append(f"- Do NOT repeat: {ops_s} — {detail}")
        sections.append("\n".join(lines))

    if not sections:
        return ""

    header = (
        "Harness (workflow guidance — prefer learned recipes & core rules "
        "over guessing ids):\n"
    )
    body = header + "\n\n".join(sections) + "\n"
    if len(body) > max_chars:
        body = body[: max_chars - 20] + "\n…[truncated]\n"
    return body


async def retrieve_harness(
    user_query: str,
    candidates: list[dict],
    *,
    tenant_id: int | None = None,
) -> HarnessContext:
    """Load static instructions + RAG recipes/anti-patterns for the query."""
    cfg = _harness_cfg()
    ctx = HarnessContext(enabled=bool(cfg.get("enabled", True)))
    if not ctx.enabled:
        return ctx

    max_chars = int(cfg.get("max_block_chars") or 3500)
    threshold = float(cfg.get("similarity_threshold") or 0.78)
    recipe_k = int(cfg.get("recipe_top_k") or 3)
    anti_k = int(cfg.get("anti_top_k") or 3)

    ctx.static_instructions = await load_instructions_async()

    if tenant_id is None:
        try:
            from core.context import current_tenant_id

            tenant_id = current_tenant_id.get()
        except Exception:
            tenant_id = 1

    query = (user_query or "").strip()
    if query:
        try:
            from core.memory import search_anti_patterns, search_plan_recipes

            ctx.recipes = await search_plan_recipes(
                query, tenant_id=int(tenant_id), top_k=recipe_k
            )
            ctx.anti_patterns = await search_anti_patterns(
                query, tenant_id=int(tenant_id), top_k=anti_k
            )
        except Exception as exc:
            logger.warning("harness: RAG retrieve failed (continuing static only): %s", exc)
            ctx.recipes = []
            ctx.anti_patterns = []

    from core_graph.harness.recipe_apply import pick_best_recipe

    ctx.best_recipe = pick_best_recipe(
        ctx.recipes, candidates, similarity_threshold=threshold
    )
    ctx.block = format_harness_block(
        static_instructions=ctx.static_instructions,
        recipes=ctx.recipes,
        anti_patterns=ctx.anti_patterns,
        max_chars=max_chars,
    )
    return ctx


def soft_apply_recipe_to_plan(
    plan: list[dict],
    harness: HarnessContext,
    candidates: list[dict],
) -> list[dict]:
    """Reorder / repair plan using best recipe when available."""
    if not harness or not harness.enabled or not harness.best_recipe:
        return plan
    ops = list(harness.best_recipe.get("op_ids") or [])
    if not ops:
        return plan
    from core_graph.harness.recipe_apply import apply_recipe_order

    return apply_recipe_order(plan or [], ops, candidates)
