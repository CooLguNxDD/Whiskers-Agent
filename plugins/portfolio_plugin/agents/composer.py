"""Composer agent — multi-block GenUI layout onto PortfolioDraft."""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.compose.blackboard import PortfolioDraft, get_draft_store
from plugins.portfolio_plugin.compose.recipes import (
    classify_specialist_goal,
    plan_for_goal,
)

logger = logging.getLogger("whiskers.plugins.portfolio_plugin.agents.composer")


async def run_composer_agent(
    *,
    draft: PortfolioDraft | None = None,
    draft_id: str | None = None,
    query: str = "",
    goal_class: str | None = None,
    theme: str = "",
    tenant_id: int,
    refresh: bool = False,
    fast_path: bool | None = None,
    previous_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Compose a multi-block layout via agentic PLE or compose_scoped_layout.

    Agentic path (redesign/bake by default): layout agent + LayoutPlan + jury.
    Fast path (scoped_ask default): compose_scoped with recipe skeleton.

    ``previous_errors`` (from a prior failed ``run_validator_agent`` call) lets
    a retry actually change behavior instead of re-running with identical
    args: a thin/flat result (``block_count_*``, ``flat_template_fallback``)
    escalates to the richer multi-block plan even for ``scoped_ask``, since
    that plan tries more block types that virtual-project fallback (indexed
    context, no DB row required) may still be able to fill.
    """
    from plugins.portfolio_plugin.compose.compose_scoped import compose_scoped_layout
    from plugins.portfolio_plugin.layout.layout_config import use_agentic_layout

    store = get_draft_store()
    d = draft or (store.get(draft_id) if draft_id else None)
    q = (query or (d.query if d else "") or (d.goal if d else "")).strip()
    if not q:
        return {
            "status": "error",
            "error": "missing_query",
            "message": "composer requires query or draft.goal",
        }

    gclass = goal_class or (classify_specialist_goal(q) if q else "scoped_ask")
    theme_s = theme or (d.theme if d else "") or ""
    prev_errs = previous_errors or []

    # Agentic Portfolio Layout Engine for redesign/bake (config-driven).
    force_agentic = any(
        str(e).startswith("jury_") or "must_fix" in str(e) or str(e).startswith("structure:")
        for e in prev_errs
    )
    if (use_agentic_layout(gclass) or force_agentic) and not (
        fast_path is True and gclass == "scoped_ask" and not force_agentic
    ):
        try:
            from plugins.portfolio_plugin.agents.layout_agent import run_layout_agent

            result = await run_layout_agent(
                draft=d,
                query=q,
                goal_class=gclass,
                theme=str(theme_s or ""),
                tenant_id=int(tenant_id),
                previous_errors=prev_errs,
                refresh=bool(refresh),
            )
            if isinstance(result, dict) and isinstance(result.get("layout"), dict):
                return result
            # fall through to deterministic if agent produced nothing
            logger.info("layout agent returned no layout; falling back to compose_scoped")
        except Exception as exc:
            logger.warning("layout agent failed, compose_scoped fallback: %s", exc)

    use_fast = fast_path if fast_path is not None else (gclass == "scoped_ask")
    block_plan = None if use_fast and gclass == "scoped_ask" else plan_for_goal(gclass, query=q)
    # Always pass explicit plan for redesign/bake; scoped may use default inside compose
    if gclass in ("bake_for_job", "redesign"):
        block_plan = plan_for_goal(gclass, query=q)

    if block_plan is None and any(
        e.startswith("block_count_") or e == "flat_template_fallback" or e == "blocks_empty"
        for e in prev_errs
    ):
        block_plan = plan_for_goal("redesign", query=q)

    try:
        result = await compose_scoped_layout(
            q,
            tenant_id=int(tenant_id),
            theme=str(theme_s or ""),
            refresh=bool(refresh),
            top_k=4,
            block_plan=block_plan,
        )
    except Exception as exc:
        logger.exception("composer agent failed")
        return {"status": "error", "error": "compose_failed", "message": str(exc)[:500]}

    layout = result.get("layout") if isinstance(result, dict) else None
    sections: list[dict] = []
    if isinstance(layout, dict):
        blocks = layout.get("blocks")
        if isinstance(blocks, list):
            sections = [b for b in blocks if isinstance(b, dict)]

    result = dict(result) if isinstance(result, dict) else {}
    result["goal_class"] = gclass
    result["section_count"] = len(sections)
    result["engine"] = "compose_scoped"

    if d is not None:
        d.query = q
        d.theme = str(theme_s) if theme_s else d.theme
        d.audience = str(result.get("audience") or d.audience or "default")
        d.sections = sections
        d.layout = layout if isinstance(layout, dict) else None
        d.phase = "compose"
        d.touch()
        result["draft"] = d.to_public()

    return result if result else {"status": "error", "error": "bad_compose_result"}
