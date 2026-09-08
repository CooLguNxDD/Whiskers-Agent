"""DESIGN.md-first deterministic floor composer.

Single fallback path for LLM-down, degraded bake, and public static layout.
Never fabricates content — only emits blocks grounded in real inventory.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from plugins.portfolio_plugin.plugin_config import SETTINGS
from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout

logger = logging.getLogger("whiskers.plugins.portfolio.floor")

# Canonical band labels mirror composer._DAG_LEVEL_BY_TYPE (matrix L0–L7).
# NOTE: design_systems/default/DESIGN.md's "Band intent" section uses an older
# band scheme (L3 Flow/L4 Stories/L5 Architecture) that has drifted from the
# canonical map here and in composer.py — _parse_band_intent() below only
# sanity-checks the section still exists; it is not the source of truth for
# what floor emits. This list is: types not in _BAND_PLAN never appear in a
# floor-composed page, so every type worth showing when the LLM is down (or
# free-mode bake seeds from the floor) must be declared here.
#
# mcpSandbox / costSim cost nothing to include (client-owned props={}) — see
# block_builder._build_db_derived. timeline / comparison / prose are added
# post-hoc by context_enrich (build_floor_layout returns the enriched dict),
# so they are deliberately not repeated here.
_BAND_PLAN: list[tuple[str, str, int]] = [
    # (band_label, block_type, top_k_hint)
    ("Intro", "hero", 1),
    ("Impact", "kpiGrid", 6),
    ("Projects", "card", 6),
    ("Architecture", "flowAnim", 4),
    ("Architecture", "mcpSandbox", 1),
    ("Architecture", "archDiagram", 4),
    ("Charts", "chart", 4),
    ("Charts", "costSim", 1),
    ("Proof", "starStory", 2),
    ("Ask", "quickActions", 1),
]

# Deterministic floor type sequence, for anti-skeleton-clone checks
# (layout_agent.py / layout_jury.py) — derived from _BAND_PLAN so it cannot
# drift out of sync with what floor actually emits, the way three separately
# hand-typed copies of this list previously could.
FLOOR_CLONE_TYPES: list[str] = [block_type for _, block_type, _ in _BAND_PLAN]


def _parse_band_intent(design_md: str) -> list[str]:
    """Extract band labels from DESIGN.md Band intent section."""
    if not design_md:
        return [b[0] for b in _BAND_PLAN]
    labels: list[str] = []
    in_section = False
    for line in design_md.splitlines():
        if re.match(r"^##\s+Band intent", line, re.I):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            m = re.match(r"^\s*-\s*L\d+\s+([^:]+):", line)
            if m:
                labels.append(m.group(1).strip())
    return labels or [b[0] for b in _BAND_PLAN]


async def build_floor_layout(
    query: str = "",
    *,
    tenant_id: int = 1,
    audience: str = "default",
    theme: str = "",
    refresh: bool = False,
    include_fish_tank: bool = False,
) -> dict[str, Any]:
    """Build a grounded, band-aware layout from DESIGN.md intent + real data.

    Returns a validated UILayout dict (or a minimal valid hero-only fallback).

    ``include_fish_tank`` is opt-in: the default public snapshot stays text-only
    (no WebGL), while the ask surface requests a tank because that is the block
    a visitor question focuses or adds a specimen to.
    """
    from plugins.portfolio_plugin.store import list_projects
    from plugins.portfolio_plugin.compose.block_builder import _build_db_derived
    from plugins.portfolio_plugin.compose.composer import (
        _hero,
        _project_cards,
        _project_grid,
        _star_stories,
        _stamp_dag_from_blocks,
        get_audience_template,
        rank_projects_by_query,
        _default_mermaid_source,
    )
    from plugins.portfolio_plugin.render.design_system import (
        auto_pick_direction,
        load_design_system,
    )

    q = (query or "").strip()
    audience_n, template = get_audience_template(audience or "default")
    star_query = template.get("star_query") or q or "impact architecture results"
    try:
        max_projects = min(20, max(1, int(template.get("max_projects", 6) or 6)))
    except (TypeError, ValueError):
        max_projects = 6

    ds = load_design_system(str(SETTINGS.get("design_system") or "default"))
    _band_labels = _parse_band_intent(str(ds.get("design_md") or ""))
    if not _band_labels:
        logger.debug("floor: DESIGN.md Band intent section missing/empty — using _BAND_PLAN defaults")

    # Theme: data-driven direction pick when query present
    theme_n = (theme or "").strip()
    if not theme_n:
        try:
            direction = auto_pick_direction(q or star_query)
            if isinstance(direction, dict):
                theme_n = str(direction.get("theme") or "")
        except Exception as exc:
            logger.warning("floor: auto_pick_direction failed (%s)", exc, exc_info=True)
            theme_n = ""

    projects: list[dict] = []
    # Unranked/untruncated inventory — the fish tank is built from all active
    # projects, not the scoped card set (see CLAUDE.md §6).
    all_projects: list[dict] = []
    try:
        proj_audience = None if audience_n == "default" else audience_n
        raw = await list_projects(audience=proj_audience, tenant_id=int(tenant_id))
        all_projects = list(raw or [])
        ranked = await rank_projects_by_query(
            raw or [],
            q or star_query,
            tenant_id=int(tenant_id),
            top_k=max(12, max_projects * 2),
        )
        projects = list(ranked or [])[:max_projects]
        if refresh:
            try:
                from plugins.portfolio_plugin.compose.composer import _live_enrich

                projects = await _live_enrich(projects, tenant_id=int(tenant_id))
            except Exception as exc:
                logger.debug("floor live_enrich fail-open: %s", exc)
    except Exception as exc:
        logger.warning("floor list_projects fail-open: %s", exc)
        projects = []

    # portfolio_star retired — floor skips starStory unless context has structured STAR meta.
    stories: list = []
    try:
        from plugins.portfolio_plugin.compose.block_builder import _resolve_star_from_context

        if star_query:
            stories = await _resolve_star_from_context(
                tenant_id=int(tenant_id),
                query=str(star_query),
                top_k=int(template.get("star_k") or 2),
            )
    except Exception as exc:
        logger.debug("floor context STAR search fail-open: %s", exc)
        stories = []

    blocks: list[dict[str, Any]] = []

    # L0 Intro — hero from settings (always present when settings exist)
    try:
        hero = _hero(SETTINGS.get("hero") if isinstance(SETTINGS, dict) else None)
        if hero is not None:
            if hasattr(hero, "model_dump"):
                blocks.append(hero.model_dump(mode="json", exclude_none=True, by_alias=True))
            elif isinstance(hero, dict):
                blocks.append(hero)
    except Exception as exc:
        logger.debug("floor hero fail-open: %s", exc)

    # L1 Impact — kpiGrid from real project metrics
    kpi_items: list[dict] = []
    for proj in projects:
        for m in proj.get("metrics") or []:
            if isinstance(m, dict) and m.get("label") and m.get("value"):
                item = {"label": str(m["label"]), "value": str(m["value"])}
                if m.get("delta"):
                    item["delta"] = str(m["delta"])
                kpi_items.append(item)
            if len(kpi_items) >= 6:
                break
        if len(kpi_items) >= 6:
            break
    if kpi_items:
        blocks.append(
            {
                "type": "kpiGrid",
                "id": "kpi-floor",
                "props": {"items": kpi_items},
            }
        )

    # L2 Projects — domain cards when available, else projectGrid
    try:
        cards = _project_cards(projects)
        if cards:
            for c in cards:
                if hasattr(c, "model_dump"):
                    blocks.append(c.model_dump(mode="json", exclude_none=True, by_alias=True))
                elif isinstance(c, dict):
                    blocks.append(c)
        else:
            pg = _project_grid(projects)
            if pg is not None:
                if hasattr(pg, "model_dump"):
                    blocks.append(pg.model_dump(mode="json", exclude_none=True, by_alias=True))
                elif isinstance(pg, dict):
                    blocks.append(pg)
    except Exception as exc:
        logger.debug("floor projects fail-open: %s", exc)

    # L3 Architecture — flowAnim (agentic motion) + mcpSandbox (interactive demo).
    # Both delegate to block_builder's DB-derived dispatch: flowAnim always
    # derives real project nodes, mcpSandbox is a zero-grounding client widget
    # (props={}) — see _BAND_PLAN comment for why these must not be skipped.
    for _btype, _tk in (("flowAnim", 4), ("mcpSandbox", 1)):
        try:
            _blk, _refs, _errs = await _build_db_derived(
                _btype,
                tenant_id=int(tenant_id),
                query=q or star_query,
                slugs=None,
                top_k=_tk,
                block_id=f"{_btype}-floor",
                props=None,
            )
            if isinstance(_blk, dict):
                blocks.append(_blk)
            elif _errs:
                logger.debug("floor %s skipped: %s", _btype, _errs)
        except Exception as exc:
            logger.debug("floor %s fail-open: %s", _btype, exc)

    # L5 Architecture — only when projects exist
    if projects:
        try:
            source = _default_mermaid_source(projects, SETTINGS)
            hero_name = (SETTINGS.get("hero") or {}).get("name") or "System architecture"
            if source:
                blocks.append(
                    {
                        "type": "archDiagram",
                        "id": "arch-floor",
                        "props": {
                            "title": str(hero_name),
                            "kind": "mermaid",
                            "source": source,
                        },
                    }
                )
        except Exception as exc:
            logger.debug("floor arch fail-open: %s", exc)

    # L3 Fish tank — opt-in only. Floor is the default-safe public snapshot and
    # must stay text/WebGL-free; the ask surface asks for the tank explicitly
    # because that is the block it focuses and adds specimens into. Built from
    # the full active inventory, not the scoped card set.
    if include_fish_tank and bool(SETTINGS.get("fish_tank_enabled")):
        try:
            from plugins.portfolio_plugin.compose.fish import build_fish_tank_block

            tank = build_fish_tank_block(all_projects or projects)
            if isinstance(tank, dict):
                blocks.append(tank)
        except Exception as exc:
            logger.debug("floor fish_tank fail-open: %s", exc)

    # L4 Charts — chart (metrics-derived) + costSim (interactive, zero grounding)
    for _btype, _tk in (("chart", 4), ("costSim", 1)):
        try:
            _blk, _refs, _errs = await _build_db_derived(
                _btype,
                tenant_id=int(tenant_id),
                query=q or star_query,
                slugs=None,
                top_k=_tk,
                block_id=f"{_btype}-floor",
                props=None,
            )
            if isinstance(_blk, dict):
                blocks.append(_blk)
            elif _errs:
                logger.debug("floor %s skipped: %s", _btype, _errs)
        except Exception as exc:
            logger.debug("floor %s fail-open: %s", _btype, exc)

    # L4 Stories — real STAR only
    try:
        star_blocks = _star_stories(stories)
        for s in star_blocks or []:
            if hasattr(s, "model_dump"):
                blocks.append(s.model_dump(mode="json", exclude_none=True, by_alias=True))
            elif isinstance(s, dict):
                blocks.append(s)
    except Exception as exc:
        logger.debug("floor star fail-open: %s", exc)

    # L7 CTA — quickActions from settings
    actions = SETTINGS.get("quick_actions") if isinstance(SETTINGS, dict) else None
    if isinstance(actions, list) and actions:
        normalized = []
        for a in actions:
            if not isinstance(a, dict):
                continue
            label, prompt = a.get("label"), a.get("prompt")
            if label and prompt:
                item = {"label": str(label), "prompt": str(prompt)}
                if a.get("icon"):
                    item["icon"] = str(a["icon"])
                normalized.append(item)
        if normalized:
            blocks.append(
                {
                    "type": "quickActions",
                    "id": "cta-floor",
                    "props": {
                        "prompt": "Ask about the work:",
                        "actions": normalized,
                    },
                }
            )

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    meta: dict[str, Any] = {
        "audience": audience_n,
        "generatedAt": generated_at,
        "composePath": "floor",
        "mode": "floor",
        "structureMode": "free",
    }
    if theme_n:
        meta["theme"] = theme_n
    if projects:
        meta["scopedProjectCount"] = len(projects)
        meta["sources"] = [
            {"ref": f"project:{p.get('slug')}", "kind": "project", "label": str(p.get("name") or p.get("slug"))}
            for p in projects if p.get("slug")
        ][:12]

    # Context enrichment: 2-col cards + timeline/prose/comparison from inventory
    try:
        from plugins.portfolio_plugin.compose.context_enrich import enrich_layout_dict

        enriched = await enrich_layout_dict(
            {"version": 1, "meta": meta, "blocks": blocks},
            tenant_id=int(tenant_id),
            projects=projects,
        )
        if isinstance(enriched, dict) and enriched.get("blocks"):
            return enriched
    except Exception as exc:
        logger.debug("floor enrich fail-open: %s", exc)

    layout_raw = {"version": 1, "meta": meta, "blocks": blocks}
    try:
        dag = _stamp_dag_from_blocks(blocks)
        if dag:
            meta["dag"] = dag
            layout_raw["meta"] = meta
    except Exception:
        logger.debug("floor.py: swallowed exception", exc_info=True)

    layout, verrs = validate_layout(layout_raw)
    if layout and not verrs:
        return layout if isinstance(layout, dict) else layout_raw
    if verrs:
        logger.warning("floor validate errors: %s", verrs[:5])
    # Return best-effort raw if validation soft-fails
    return layout_raw if blocks else {
        "version": 1,
        "meta": meta,
        "blocks": [
            {
                "type": "hero",
                "id": "hero-1",
                "props": {
                    "name": (SETTINGS.get("hero") or {}).get("name") or "Portfolio",
                    "tagline": (SETTINGS.get("hero") or {}).get("tagline") or "",
                },
            }
        ],
    }


def floor_to_block_plan(layout: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Convert a floor layout into LayoutPlan-style block_plan steps."""
    if not isinstance(layout, dict):
        return []
    steps: list[dict[str, Any]] = []
    for i, b in enumerate(layout.get("blocks") or []):
        if not isinstance(b, dict):
            continue
        t = str(b.get("type") or "").strip()
        if not t:
            continue
        steps.append(
            {
                "block_type": t,
                "block_id": str(b.get("id") or f"{t}-{i + 1}"),
                "top_k": 4,
                "kind": "db",
            }
        )
    return steps
