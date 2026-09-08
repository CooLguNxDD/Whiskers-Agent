"""Soft-apply learned plan recipes onto a GOAP/linear plan skeleton."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("whiskers.harness")


def recipe_ops_available(
    op_ids: list[str],
    candidates: list[dict],
) -> bool:
    """True when every recipe op is present in this turn's candidates."""
    if not op_ids:
        return False
    available = {c.get("operation_id") for c in (candidates or []) if c.get("operation_id")}
    return all(op in available for op in op_ids)


def pick_best_recipe(
    recipes: list[dict],
    candidates: list[dict],
    *,
    similarity_threshold: float = 0.78,
) -> dict | None:
    """Return highest-similarity recipe whose ops ⊆ candidates and above threshold."""
    best: dict | None = None
    best_sim = -1.0
    for r in recipes or []:
        if not isinstance(r, dict):
            continue
        sim = float(r.get("similarity") or 0.0)
        if sim < similarity_threshold:
            continue
        ops = list(r.get("op_ids") or [])
        if not recipe_ops_available(ops, candidates):
            continue
        if sim > best_sim:
            best_sim = sim
            best = r
    return best


def apply_recipe_order(
    steps: list[dict],
    recipe_op_ids: list[str],
    candidates: list[dict],
) -> list[dict]:
    """Reorder plan steps to match recipe op sequence when possible.

    Steps whose op is in the recipe are ordered by recipe index; other steps
    keep relative order after the recipe block. Does not invent missing ops —
    use soft skeleton builder for that. Runs collection→by-id repair after.
    """
    if not steps or not recipe_op_ids:
        return steps

    recipe_index = {op: i for i, op in enumerate(recipe_op_ids)}
    in_recipe: list[tuple[int, dict]] = []
    others: list[dict] = []
    for s in steps:
        op = s.get("operation_id")
        if op in recipe_index:
            in_recipe.append((recipe_index[op], dict(s)))
        else:
            others.append(dict(s))

    if not in_recipe:
        return steps

    in_recipe.sort(key=lambda t: t[0])
    ordered = [s for _, s in in_recipe] + others

    # Re-bind sequential depends_on loosely: later recipe steps depend on previous
    for i in range(1, len(in_recipe)):
        step = ordered[i]
        deps = step.get("depends_on")
        if deps is None:
            step["depends_on"] = i - 1
        ordered[i] = step

    try:
        from core_graph.goap.integrate.chain_repair import repair_collection_consumer_chain

        ordered = repair_collection_consumer_chain(ordered, candidates)
    except Exception as exc:
        logger.debug("recipe_apply chain_repair skipped: %s", exc)

    return ordered


def build_skeleton_from_recipe(
    recipe_op_ids: list[str],
    candidates: list[dict],
) -> list[dict]:
    """Build a minimal plan skeleton from recipe ops present in candidates.

    Used when GOAP returns empty/None but a high-confidence recipe matches, or
    to replace a clearly wrong dual-goal plan when soft-apply is forced.
    """
    by_op = {c["operation_id"]: c for c in (candidates or []) if c.get("operation_id")}
    steps: list[dict] = []
    for i, op in enumerate(recipe_op_ids or []):
        cand = by_op.get(op)
        if not cand:
            continue
        step: dict[str, Any] = {
            "operation_id": op,
            "plugin_id": cand.get("plugin_id"),
            "is_fast_path": bool(cand.get("is_fast_path")),
            "intent": cand.get("description") or op,
            "args": {},
            "arg_bindings": {},
            "depends_on": (i - 1) if i > 0 else None,
            "model": cand.get("model"),
        }
        steps.append(step)

    if len(steps) >= 2:
        try:
            from core_graph.goap.integrate.chain_repair import repair_collection_consumer_chain

            steps = repair_collection_consumer_chain(steps, candidates)
        except Exception as exc:
            logger.debug("skeleton chain_repair skipped: %s", exc)

    return steps
