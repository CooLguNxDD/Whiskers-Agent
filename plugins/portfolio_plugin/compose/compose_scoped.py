"""Server-side retrieve → build → assemble for scoped GenUI layouts.

Replaces hardcoded regex fragment routing for public Ask / layout-for-query.
Deterministic (no LLM): ranks projects, builds validated blocks via
``build_layout_block_impl``, assembles with ``compose_custom_layout``.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.compose.block_builder import build_layout_block_impl
from plugins.portfolio_plugin.compose.composer import (
    compose_custom_layout,
    compose_layout,
    get_audience_template,
    infer_audience_from_job_signals,
)

logger = logging.getLogger("whiskers.plugins.portfolio")

# Matrix default (Open Design level-row): Intro → Impact → Projects(cards) → proof.
# ``card`` is DB-derived (one tile per ranked project). Agent can override via tools.
_DEFAULT_PLAN: list[dict[str, Any]] = [
    {"block_type": "hero", "block_id": "h1", "top_k": 1},
    {"block_type": "kpiGrid", "block_id": "kpi-master", "top_k": 4},
    {"block_type": "card", "top_k": 4},
    {"block_type": "archDiagram", "top_k": 4},
    {"block_type": "starStory", "top_k": 1},
    {"block_type": "quickActions", "top_k": 1},
]

# Ask mode focuses/adds specimens inside the fishTank block, so a scoped layout
# without one gives a visitor question nothing to patch. Inserted before the
# CTA when the tank is enabled; the base plan stays untouched for tests and
# for deployments that keep the tank off.
_FISH_TANK_STEP: dict[str, Any] = {
    "block_type": "fishTank",
    "block_id": "fish-tank-1",
    "top_k": 20,
}


def default_block_plan() -> list[dict[str, Any]]:
    """``_DEFAULT_PLAN`` plus the fishTank step when ``fish_tank_enabled``."""
    from plugins.portfolio_plugin.plugin_config import SETTINGS

    plan = [dict(step) for step in _DEFAULT_PLAN]
    if not bool(SETTINGS.get("fish_tank_enabled")):
        return plan
    cta = next(
        (i for i, s in enumerate(plan) if s.get("block_type") == "quickActions"),
        len(plan),
    )
    plan.insert(cta, dict(_FISH_TANK_STEP))
    return plan


async def compose_scoped_layout(
    query: str,
    *,
    tenant_id: int = 1,
    theme: str = "",
    refresh: bool = False,
    top_k: int = 3,
    block_plan: list[dict[str, Any]] | None = None,
    audience: str = "",
) -> dict[str, Any]:
    """Compose a scoped, multi-column layout from a free-text query.

    ``audience``, when non-empty and a recognized audience id, overrides the
    4-token keyword scorer (``infer_audience_from_job_signals``) that would
    otherwise re-derive it from the query text alone — this is how a plan's
    ``LayoutPlan.audience`` (previously discarded entirely) actually reaches
    the composed layout.

    Returns ``{status, layout, audience, star_query, mode, scoped_project_count?}``.
    Falls back to audience ``compose_layout`` if no blocks can be built.
    """
    if not query or not str(query).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["query"],
        }

    q = str(query).strip()
    audience_override = str(audience or "").strip().lower()
    star_query: str | None = None
    if audience_override:
        audience_n, template = get_audience_template(audience_override)
        if audience_n != audience_override:
            audience_n = None  # not a recognized audience id — fall through to inference
    else:
        audience_n = None
    if audience_n is None:
        inferred, star_query = infer_audience_from_job_signals(q)
        audience_n, template = get_audience_template(inferred)
    if not star_query:
        star_query = template.get("star_query") or q

    plan = (
        list(block_plan)
        if isinstance(block_plan, list) and block_plan
        else default_block_plan()
    )
    sections: list[dict[str, Any]] = []
    all_refs: list[str] = []
    step_errors: list[str] = []
    seen_refs: set[str] = set()
    seen_star_fps: set[tuple[str, str, str, str]] = set()
    scoped_count = 0
    # Plan-driven matrix bands (Phase 2.1): accumulated post-materialization
    # since a step (e.g. "card") can expand into N builder-generated block
    # ids the plan can't name in advance. Falls back to composer's
    # type->band stamp when no step declares a band (band_buckets empty).
    band_buckets: dict[int, dict[str, Any]] = {}
    seen_band_ids: set[str] = set()

    def _add_to_band(band: dict[str, Any] | None, ids: list[str]) -> None:
        if not band or not ids:
            return
        try:
            level = int(band.get("level"))
        except (TypeError, ValueError):
            return
        label = str(band.get("label") or "").strip()
        if not label:
            return
        bucket = band_buckets.setdefault(level, {"level": level, "label": label, "nodes": []})
        cols = band.get("cols")
        if isinstance(cols, int) and 1 <= cols <= 4:
            bucket["cols"] = cols
        for bid in ids:
            bid = str(bid or "").strip()
            # First band wins on a duplicate id -- mirrors composer's
            # _stamp_dag_from_blocks, which never assigns one block id to
            # two bands (the FE's "used" tracking would silently drop it).
            if bid and bid not in seen_band_ids:
                seen_band_ids.add(bid)
                bucket["nodes"].append(bid)

    for i, step in enumerate(plan):
        if not isinstance(step, dict):
            continue
        btype = str(step.get("block_type") or step.get("type") or "").strip()
        if not btype:
            continue
        step_top_k = int(step.get("top_k") or top_k or 3)
        step_top_k = max(1, min(step_top_k, 20))
        slugs = step.get("slugs") if isinstance(step.get("slugs"), list) else None
        layout = step.get("layout") if isinstance(step.get("layout"), dict) else None
        # Default half-width for project cards → 2-up matrix (L2 density).
        if btype == "card" and layout is None:
            layout = {"span": 6}
        props = step.get("props") if isinstance(step.get("props"), dict) else None
        source_refs = step.get("source_refs") if isinstance(step.get("source_refs"), list) else None
        step_kind = str(step.get("kind") or "auto")
        step_band = step.get("band") if isinstance(step.get("band"), dict) else None
        # Default Projects band to 2 columns when agent omits band.cols
        if btype == "card" and step_band is None:
            step_band = {"level": 2, "label": "Projects", "cols": 2}
        elif btype == "card" and isinstance(step_band, dict) and step_band.get("cols") is None:
            step_band = {**step_band, "cols": 2}
        # STAR / arch / grids use the visitor query for scoping.
        step_query = str(step.get("query") or q)

        try:
            res = await build_layout_block_impl(
                btype,
                tenant_id=tenant_id,
                query=step_query,
                slugs=slugs,
                top_k=step_top_k,
                props=props,
                source_refs=source_refs,
                block_id=str(step.get("block_id") or f"{btype}-{i}"),
                layout=layout,
                kind=step_kind,
            )
        except Exception as exc:
            logger.warning("compose_scoped: build %s failed: %s", btype, exc)
            step_errors.append(f"{btype}: {exc}"[:300])
            continue

        if not isinstance(res, dict) or res.get("status") != "ok":
            build_errs = (res or {}).get("errors") if isinstance(res, dict) else res
            logger.debug("compose_scoped: skip %s: %s", btype, build_errs)
            step_errors.append(f"{btype}: {build_errs}"[:300])
            continue

        block = res.get("block")
        if not isinstance(block, dict):
            continue

        # Drop duplicate STAR stories (same S/T/A/R fingerprint). Two plan steps
        # with top_k that collapse to one vector hit used to render twice.
        if btype == "starStory":
            props = block.get("props") if isinstance(block.get("props"), dict) else {}
            fp = (
                str(props.get("situation") or "").strip(),
                str(props.get("task") or "").strip(),
                str(props.get("action") or "").strip(),
                str(props.get("result") or "").strip(),
            )
            if fp != ("", "", "", "") and fp in seen_star_fps:
                step_errors.append(
                    f"starStory: skipped duplicate STAR fingerprint at step {i}"
                )
                continue
            if fp != ("", "", "", ""):
                seen_star_fps.add(fp)

        sections.append(block)

        if btype == "projectGrid":
            projects = (block.get("props") or {}).get("projects") or []
            if isinstance(projects, list):
                scoped_count = max(scoped_count, len(projects))
        if btype == "card":
            scoped_count = max(scoped_count, 1)
            # Builder may return a list of cards under multi_blocks.
            multi = res.get("blocks") if isinstance(res.get("blocks"), list) else None
            if multi:
                # Replace the single placeholder section entry with the
                # expanded list — only if it's still the one we just
                # appended (identity check, not a bare pop-by-position).
                if sections and sections[-1] is block:
                    sections.pop()
                for mb in multi:
                    if isinstance(mb, dict):
                        sections.append(mb)
                scoped_count = max(scoped_count, len(multi))
                _add_to_band(step_band, [str((mb or {}).get("id") or "") for mb in multi if isinstance(mb, dict)])
                # Already counted refs below from res
                for r in res.get("source_refs") or []:
                    r_str = str(r).strip()
                    if r_str and r_str not in seen_refs:
                        seen_refs.add(r_str)
                        all_refs.append(r_str)
                continue

        _add_to_band(step_band, [str(block.get("id") or "")])

        for r in res.get("source_refs") or []:
            r_str = str(r).strip()
            if r_str and r_str not in seen_refs:
                seen_refs.add(r_str)
                all_refs.append(r_str)

    dag: dict[str, Any] | None = None
    if band_buckets:
        levels = [band_buckets[k] for k in sorted(band_buckets.keys()) if band_buckets[k]["nodes"]]
        if levels:
            n = max(1, len(levels) - 1)
            for i, lvl in enumerate(levels):
                lvl["at"] = round(i / n, 2)
            dag = {"levels": levels}

    if not sections:
        # Fail-open: audience template (no regex fragment stack).
        layout = await compose_layout(
            audience=audience_n,
            star_query=star_query,
            tenant_id=tenant_id,
            refresh=refresh,
            _skip_scoped=True,
        )
        if theme and str(theme).strip() and isinstance(layout, dict):
            meta = dict(layout.get("meta") or {})
            meta["theme"] = str(theme).strip()
            meta["mode"] = "template"
            layout = {**layout, "meta": meta}
        elif isinstance(layout, dict):
            meta = dict(layout.get("meta") or {})
            meta["mode"] = "template"
            layout = {**layout, "meta": meta}
        out: dict[str, Any] = {
            "status": "ok",
            "layout": layout,
            "audience": audience_n,
            "star_query": star_query,
            "mode": "template",
            "scoped_project_count": 0,
        }
        if step_errors:
            out["step_errors"] = step_errors
        return out

    theme_s = str(theme).strip() if theme else ""
    label = f"Agentically curated · {scoped_count} project(s) scoped · grounded"
    spec: dict[str, Any] = {
        "audience": audience_n,
        "star_query": star_query,
        "sections": sections,
        "mode": "scoped",
        "curationLabel": label,
        "scopedProjectCount": scoped_count,
        "refresh": bool(refresh),
    }
    if theme_s:
        spec["theme"] = theme_s
    if dag:
        # compose_custom_layout honors an explicit meta.dag and skips its own
        # type->band stamp when this key is present -- see composer.py's
        # compose_custom_layout / _stamp_dag_from_blocks fallback.
        spec["dag"] = dag

    layout, errors = await compose_custom_layout(spec, tenant_id=tenant_id, preset="")
    if errors or layout is None:
        logger.info("compose_scoped: assemble failed, template fallback: %s", errors)
        layout = await compose_layout(
            audience=audience_n,
            star_query=star_query,
            tenant_id=tenant_id,
            refresh=refresh,
            _skip_scoped=True,
        )
        if theme_s and isinstance(layout, dict):
            meta = dict(layout.get("meta") or {})
            meta["theme"] = theme_s
            meta["mode"] = "template"
            layout = {**layout, "meta": meta}
        out = {
            "status": "ok",
            "layout": layout,
            "audience": audience_n,
            "star_query": star_query,
            "mode": "template",
            "errors": errors,
            "scoped_project_count": scoped_count,
        }
        if step_errors:
            out["step_errors"] = step_errors
        return out

    # Ensure citations from builds land even if blocks lacked _sourceRefs markers.
    if isinstance(layout, dict) and all_refs:
        meta = dict(layout.get("meta") or {})
        existing = meta.get("sources") or []
        seen = {
            str(s.get("ref") if isinstance(s, dict) else s).strip()
            for s in existing
            if s
        }
        sources = list(existing) if isinstance(existing, list) else []
        for r in all_refs:
            if r not in seen:
                seen.add(r)
                sources.append({"ref": r})
        if sources:
            meta["sources"] = sources
        if "mode" not in meta:
            meta["mode"] = "scoped"
        if "curationLabel" not in meta:
            meta["curationLabel"] = label
        if "scopedProjectCount" not in meta:
            meta["scopedProjectCount"] = scoped_count
        layout = {**layout, "meta": meta}

    # 2-col project cards + extra blocks grounded in project inventory
    if isinstance(layout, dict):
        try:
            from plugins.portfolio_plugin.compose.context_enrich import enrich_layout_dict

            enriched = await enrich_layout_dict(layout, tenant_id=int(tenant_id))
            if isinstance(enriched, dict) and enriched.get("blocks"):
                layout = enriched
                # keep scoped_count honest after enrich
                n_cards = sum(
                    1
                    for b in (layout.get("blocks") or [])
                    if isinstance(b, dict) and b.get("type") == "card"
                )
                if n_cards:
                    scoped_count = max(scoped_count, n_cards)
        except Exception as exc:
            logger.debug("compose_scoped enrich fail-open: %s", exc)

    out = {
        "status": "ok",
        "layout": layout,
        "audience": audience_n,
        "star_query": star_query,
        "mode": "scoped",
        "scoped_project_count": scoped_count,
    }
    if step_errors:
        out["step_errors"] = step_errors
    return out
