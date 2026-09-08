"""Validator agent — schema-validate layout; drive compose retries."""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.compose.blackboard import PortfolioDraft, get_draft_store
from plugins.portfolio_plugin.compose.recipes import classify_specialist_goal, layout_quality_ok

logger = logging.getLogger("whiskers.plugins.portfolio_plugin.agents.validator")

MAX_VALIDATE_RETRIES = 2


async def run_validator_agent(
    *,
    draft: PortfolioDraft | None = None,
    draft_id: str | None = None,
    layout: dict[str, Any] | None = None,
    goal_class: str | None = None,
    query: str = "",
) -> dict[str, Any]:
    """Validate layout schema + quality bar; return errors for compose retry."""
    from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout

    store = get_draft_store()
    d = draft or (store.get(draft_id) if draft_id else None)
    lay = layout if isinstance(layout, dict) else (d.layout if d else None)
    q = query or (d.query if d else "") or (d.goal if d else "")
    gclass = goal_class or classify_specialist_goal(q)

    if not isinstance(lay, dict):
        errs = ["layout_missing"]
        if d is not None:
            d.validation_errors = errs
            d.phase = "validate"
            d.touch()
        return {
            "status": "error",
            "error": "layout_missing",
            "errors": errs,
            "goal_class": gclass,
            "should_retriage": True,
            "retriage_reason": "specialist_compose_failed",
        }

    validated, schema_errors = validate_layout(lay)
    quality_ok, quality_errs = layout_quality_ok(lay, goal_class=gclass)
    all_errs = list(schema_errors or []) + list(quality_errs or [])

    if d is not None:
        d.validation_errors = all_errs
        if validated is not None:
            d.layout = validated
        d.phase = "validate"
        d.touch()

    if all_errs:
        return {
            "status": "error",
            "error": "validation_failed",
            "errors": all_errs,
            "goal_class": gclass,
            "should_retriage": len(all_errs) > 0,
            "retriage_reason": "specialist_compose_failed",
            "draft": d.to_public() if d else None,
        }

    return {
        "status": "ok",
        "layout": validated or lay,
        "goal_class": gclass,
        "errors": [],
        "draft": d.to_public() if d else None,
    }


async def validate_with_retries(
    *,
    draft: PortfolioDraft,
    goal_class: str,
    tenant_id: int,
    theme: str = "",
    max_retries: int = MAX_VALIDATE_RETRIES,
) -> dict[str, Any]:
    """Compose → schema/quality → jury loop (max retries) using composer agent."""
    from plugins.portfolio_plugin.agents.composer import run_composer_agent
    from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config
    from plugins.portfolio_plugin.layout.layout_jury import critique_layout

    cfg = get_portfolio_layout_config()
    # Layout agent already multi-rounds jury; keep outer retries modest.
    last: dict[str, Any] = {}
    prev_errors: list[str] = []
    best_layout = draft.layout if isinstance(draft.layout, dict) else None
    best_jury: dict[str, Any] | None = None

    for attempt in range(max_retries + 1):
        if attempt == 0 and draft.layout is None:
            last = await run_composer_agent(
                draft=draft,
                query=draft.query or draft.goal,
                goal_class=goal_class,
                theme=theme,
                tenant_id=tenant_id,
                refresh=attempt > 0,
                previous_errors=prev_errors,
            )
            if last.get("status") == "error" and draft.layout is None:
                return last
        elif attempt > 0:
            last = await run_composer_agent(
                draft=draft,
                query=draft.query or draft.goal,
                goal_class=goal_class,
                theme=theme,
                tenant_id=tenant_id,
                refresh=True,
                previous_errors=prev_errors,
            )
            if last.get("status") == "error" and draft.layout is None:
                return last

        v = await run_validator_agent(draft=draft, goal_class=goal_class)
        if v.get("status") != "ok":
            last = v
            prev_errors = list(v.get("errors") or [])
            continue

        # Multi-dim jury on top of schema/min-block gates
        lay = draft.layout if isinstance(draft.layout, dict) else v.get("layout")
        jury = await critique_layout(
            lay if isinstance(lay, dict) else None,
            query=draft.query or draft.goal or "",
            goal_class=goal_class,
            theme=theme,
            threshold=float(cfg.get("jury_threshold") or 7.5),
        )
        if isinstance(lay, dict):
            score = float(jury.get("composite") or 0.0)
            if best_layout is None or score >= float((best_jury or {}).get("composite") or -1):
                best_layout = lay
                best_jury = jury

        if jury.get("passed") or last.get("ship_best") or last.get("engine") == "layout_agent":
            # layout_agent already exhausted plan rounds — accept if schema-ok
            if jury.get("passed") or last.get("engine") == "layout_agent":
                v["attempts"] = attempt + 1
                v["jury"] = jury
                if not jury.get("passed") and last.get("engine") == "layout_agent":
                    v["ship_best"] = True
                return v

        prev_errors = list(jury.get("must_fix") or []) + list(v.get("errors") or [])
        last = {**v, "status": "error", "error": "jury_failed", "jury": jury, "errors": prev_errors}

    if best_layout is not None:
        draft.layout = best_layout
        draft.touch()
        return {
            "status": "ok",
            "layout": best_layout,
            "goal_class": goal_class,
            "errors": [],
            "attempts": max_retries + 1,
            "ship_best": True,
            "jury": best_jury,
            "draft": draft.to_public(),
        }

    last["attempts"] = max_retries + 1
    last["should_retriage"] = True
    last["retriage_reason"] = "specialist_compose_failed"
    return last
