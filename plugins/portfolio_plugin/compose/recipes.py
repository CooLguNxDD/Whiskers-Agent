"""Block recipes for specialist compose / bake quality bar.

YAML page recipes under ``plugins/portfolio_plugin/recipes/`` are preferred via
``recipe_registry``; the constants below remain as fallbacks.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.recipes")

# Quality bar: richer free-structure mix (agent may deviate freely in free mode).
# Cards default to span 6 (2 per row). Prefer catalog diversity over thin pages.
BAKE_REDESIGN_PLAN: list[dict[str, Any]] = [
    {"block_type": "hero", "block_id": "h1", "top_k": 1},
    {"block_type": "kpiGrid", "block_id": "kpi-master", "top_k": 6},
    {
        "block_type": "card",
        "top_k": 6,
        "layout": {"span": 6},
        "band": {"level": 2, "label": "Projects", "cols": 2},
    },
    {"block_type": "flowAnim", "block_id": "fa1", "top_k": 4},
    {"block_type": "timeline", "top_k": 4},
    {"block_type": "chart", "top_k": 4},
    {"block_type": "starStory", "top_k": 1},
    {"block_type": "starStory", "top_k": 2},
    {"block_type": "archDiagram", "top_k": 4},
    {"block_type": "comparison", "top_k": 4},
    {"block_type": "quickActions", "top_k": 1},
]

SCOPED_ASK_PLAN: list[dict[str, Any]] = [
    {"block_type": "hero", "block_id": "h1", "top_k": 1},
    {"block_type": "kpiGrid", "block_id": "kpi-master", "top_k": 4},
    {
        "block_type": "card",
        "top_k": 4,
        "layout": {"span": 6},
        "band": {"level": 2, "label": "Projects", "cols": 2},
    },
    {"block_type": "timeline", "top_k": 3},
    {"block_type": "starStory", "top_k": 1},
    {"block_type": "archDiagram", "top_k": 3},
]

MIN_BLOCKS_BAKE = 6
MIN_BLOCKS_REDESIGN = 5
MIN_BLOCK_TYPES = 4


def classify_specialist_goal(query: str) -> str:
    """Classify portfolio goal: discover | redesign | bake_for_job | scoped_ask."""
    q = (query or "").lower()
    if any(k in q for k in ("bake", "job layout", "for the job", "short_id", "?j=", "tailor for")):
        return "bake_for_job"
    if any(k in q for k in ("discover", "reindex", "index github", "index notion", "refresh inventory")):
        return "discover"
    if any(k in q for k in ("redesign", "re-layout", "relayout", "full layout", "rebuild layout")):
        return "redesign"
    return "scoped_ask"


def plan_for_goal(goal_class: str, query: str = "") -> list[dict[str, Any]]:
    """Return block_plan for compose_scoped_layout.

    Prefers skill-meta quality targets as a lightweight plan; falls back to
    Python constants. Floor composer owns full LLM-down layouts.
    """
    try:
        from plugins.portfolio_plugin.layout.skill_meta import match_skill_for_goal

        meta = match_skill_for_goal(goal_class, query or "")
        if meta and meta.quality:
            # Prefer rich default plans; skill quality is a floor not a skeleton.
            pass
    except Exception:
        # Skill meta optional; fall through to Python constants.
        logger.debug("recipes.plan_for_goal: skill_meta lookup failed", exc_info=True)
    if goal_class in ("bake_for_job", "redesign"):
        return list(BAKE_REDESIGN_PLAN)
    return list(SCOPED_ASK_PLAN)


def min_blocks_for(goal_class: str) -> int:
    """Returns the minimum structural block count required to pass validation for a given goal class."""
    if goal_class == "bake_for_job":
        return MIN_BLOCKS_BAKE
    if goal_class == "redesign":
        return MIN_BLOCKS_REDESIGN
    return 3


def layout_quality_ok(layout: dict[str, Any] | None, *, goal_class: str) -> tuple[bool, list[str]]:
    """Min-block / type diversity gate for every specialist goal class.

    Thin shim over ``bake.contract.structural_quality`` so the structural rules
    live in one place. This is the **structural subset** only — it deliberately
    does not check schema validity, grounding or the jury. For "is this fit to
    send a recruiter", use ``bake.contract.assess_bake_quality``.

    Previously only ``bake_for_job``/``redesign`` were gated — ``scoped_ask``
    always reported ok regardless of block count, so a flat template-mode
    fallback (hero + STAR only, ``meta.mode == "template"``) silently passed
    validation instead of triggering a compose retry / retriage.
    """
    # Local import: contract imports this module for the shared rule constants.
    from plugins.portfolio_plugin.bake.contract import structural_quality

    return structural_quality(layout, goal_class=goal_class)
