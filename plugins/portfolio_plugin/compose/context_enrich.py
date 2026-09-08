"""Post-compose enrichment: more blocks grounded in project / index context.

Runs after materialize / floor so agent plans and deterministic floors both
get denser evidence-backed sections without inventing metrics.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.context_enrich")

# Soft caps so enrichment never blows the page.
_MAX_EXTRA_PROSE = 3
# Raised from 18 -> 20 to fit the interactive widget pair (mcpSandbox/costSim)
# alongside everything else enrichment already adds (bake-parity fix).
_MAX_TOTAL_BLOCKS = 20
_MIN_SUMMARY_FOR_PROSE = 180

# Block types whose content list can validate as empty (schema defaults to
# ``[]``) but ships as a useless title-only shell — see
# plugins/portfolio_plugin/schema/ui_layout_schema.py's *Props defaults.
# block_builder rejects these on the agent-authored path now, but this stays
# as defense-in-depth for any block that reaches enrich already empty (e.g.
# a DB-derived block whose source data was thin).
_EMPTY_CONTENT_KEY: dict[str, str] = {
    "comparison": "rows",
    "chart": "series",
    "timeline": "items",
    "kpiGrid": "items",
}


def _block_types(blocks: list[dict]) -> set[str]:
    return {str(b.get("type") or "") for b in blocks if isinstance(b, dict)}


def _ensure_card_spans(blocks: list[dict]) -> list[dict]:
    """Force project cards to half-width (2-up) unless already spanned."""
    out: list[dict] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if str(b.get("type") or "") != "card":
            out.append(b)
            continue
        layout = b.get("layout") if isinstance(b.get("layout"), dict) else {}
        if layout.get("span") is None:
            b = {**b, "layout": {**layout, "span": 6}}
        out.append(b)
    return out


def _prose_from_project(proj: dict, *, idx: int) -> dict[str, Any] | None:
    summary = str(proj.get("summary") or "").strip()
    if len(summary) < _MIN_SUMMARY_FOR_PROSE:
        return None
    slug = str(proj.get("slug") or f"p{idx}")
    name = str(proj.get("name") or slug)
    safe = re.sub(r"[^a-z0-9_-]+", "-", slug.lower()).strip("-") or f"p{idx}"
    # Prefer first ~2 paragraphs for readable deep dive.
    paras = [p.strip() for p in summary.split("\n\n") if p.strip()]
    body = "\n\n".join(paras[:3]) if paras else summary
    if len(body) > 1200:
        body = body[:1199].rstrip() + "…"
    sources = proj.get("context_sources") if isinstance(proj.get("context_sources"), list) else []
    refs = []
    for s in sources[:4]:
        if isinstance(s, dict) and s.get("ref"):
            refs.append(str(s["ref"]))
        elif isinstance(s, dict) and s.get("id"):
            refs.append(str(s["id"]))
    md = f"### Deep dive · {name}\n\n{body}"
    return {
        "type": "prose",
        "id": f"prose-{safe}",
        "props": {"markdown": md},
        "layout": {"span": 12},
        "_sourceRefs": refs or [f"project:{slug}"],
    }


def _timeline_from_projects(projects: list[dict]) -> dict[str, Any] | None:
    from plugins.portfolio_plugin.discovery.period import format_period

    try:
        from plugins.portfolio_plugin.compose.display_copy import author_display_copy
        from plugins.portfolio_plugin.compose.quality import filter_projects_for_layout

        projects = filter_projects_for_layout(projects)
    except Exception:
        author_display_copy = None  # type: ignore[assignment]
    # Newest first — falls back to insertion order when no project has a
    # known date (sorts equal, Python sort is stable).
    projects = sorted(
        projects,
        key=lambda p: (p.get("ended_on") or p.get("started_on") or "") if isinstance(p, dict) else "",
        reverse=True,
    )
    items: list[dict[str, Any]] = []
    for i, proj in enumerate(projects[:6]):
        if not isinstance(proj, dict):
            continue
        name = str(proj.get("name") or proj.get("slug") or f"Project {i + 1}")
        if author_display_copy is not None:
            blurb = author_display_copy(proj, form="fish_blurb")
        else:
            summary = str(proj.get("summary") or "").strip()
            blurb = (summary[:200] + ("…" if len(summary) > 200 else "")) or None
        if not blurb:
            continue
        tags = proj.get("tags") if isinstance(proj.get("tags"), list) else []
        item: dict[str, Any] = {
            "date": format_period(proj.get("started_on"), proj.get("ended_on")) or "shipped",
            "title": name,
            "body": blurb,
        }
        if tags:
            item["tag"] = str(tags[0])
        items.append({k: v for k, v in item.items() if v is not None})
    if len(items) < 2:
        return None
    return {
        "type": "timeline",
        "id": "timeline-context",
        "props": {"title": "Delivery arc", "items": items},
        "layout": {"span": 12},
    }


def _comparison_from_projects(projects: list[dict]) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for proj in projects[:4]:
        if not isinstance(proj, dict):
            continue
        name = str(proj.get("name") or proj.get("slug") or "Project")
        metrics = proj.get("metrics") if isinstance(proj.get("metrics"), list) else []
        tags = proj.get("tags") if isinstance(proj.get("tags"), list) else []
        cells: list[str] = []
        for m in metrics[:2]:
            if isinstance(m, dict) and m.get("value") is not None:
                cells.append(f"{m.get('label') or 'metric'}: {m.get('value')}")
        if not cells and tags:
            cells.append(str(tags[0]))
        while len(cells) < 2:
            cells.append("—")
        rows.append({"label": name, "cells": cells[:2]})
    if len(rows) < 2:
        return None
    return {
        "type": "comparison",
        "id": "compare-projects",
        "props": {
            "title": "Project contrast",
            "columns": [{"label": "Signal"}, {"label": "Domain"}],
            "rows": rows,
        },
        "layout": {"span": 12},
    }


def _is_empty_shell(b: dict) -> bool:
    key = _EMPTY_CONTENT_KEY.get(str(b.get("type") or ""))
    if not key:
        return False
    props = b.get("props") if isinstance(b.get("props"), dict) else {}
    val = props.get(key)
    return not isinstance(val, list) or len(val) == 0


def _chart_from_projects(projects: list[dict]) -> dict[str, Any] | None:
    """Metrics-derived bar chart — same numeric-extraction pattern as
    block_builder._build_db_derived's chart fallback, kept pure/sync here so
    enrich never needs an async round trip to repair a thin chart."""
    pts: list[dict[str, Any]] = []
    for p in projects:
        for m in (p.get("metrics") or [])[:2]:
            if not (isinstance(m, dict) and m.get("label") and m.get("value") is not None):
                continue
            nums = re.findall(r"\d+\.?\d*", str(m["value"]))
            if nums:
                try:
                    pts.append({"x": str(m["label"])[:14], "y": float(nums[0])})
                except ValueError:
                    pass
            if len(pts) >= 6:
                break
        if len(pts) >= 6:
            break
    if not pts:
        return None
    return {
        "type": "chart",
        "id": "chart-context",
        "props": {"kind": "bar", "title": "Metrics", "series": [{"name": "metrics", "points": pts}]},
        "layout": {"span": 12},
    }


def _kpigrid_from_projects(projects: list[dict]) -> dict[str, Any] | None:
    items: list[dict[str, Any]] = []
    for p in projects:
        for m in p.get("metrics") or []:
            if isinstance(m, dict) and m.get("label") and m.get("value"):
                item = {"label": str(m["label"]), "value": str(m["value"])}
                if m.get("delta"):
                    item["delta"] = str(m["delta"])
                items.append(item)
            if len(items) >= 6:
                break
        if len(items) >= 6:
            break
    if not items:
        return None
    return {"type": "kpiGrid", "id": "kpi-context", "props": {"items": items}}


_EMPTY_SHELL_REPAIR = {
    "comparison": lambda projs: _comparison_from_projects(projs),
    "timeline": lambda projs: _timeline_from_projects(projs),
    "chart": lambda projs: _chart_from_projects(projs),
    "kpiGrid": lambda projs: _kpigrid_from_projects(projs),
}


def _repair_or_drop_empty_shells(blocks: list[dict], projects: list[dict]) -> list[dict]:
    """Fill an empty-content block from real project data, or drop it.

    A block that validates (schema defaults its content list to ``[]``) but
    ships with no actual rows/series/items reads as a broken page (see the
    Playwright bake-parity finding — an empty title-only comparison table).
    Never fabricates: repair only reuses real project metrics/tags via the
    same helpers this module already uses to build these blocks from scratch.
    """
    out: list[dict] = []
    for b in blocks:
        if not isinstance(b, dict) or not _is_empty_shell(b):
            if isinstance(b, dict):
                out.append(b)
            continue
        btype = str(b.get("type") or "")
        repair = _EMPTY_SHELL_REPAIR.get(btype)
        repaired = repair(projects) if repair else None
        if repaired is not None:
            out.append({**repaired, "id": b.get("id") or repaired.get("id")})
        else:
            logger.debug("enrich: dropping empty %s shell (no project data to repair it)", btype)
    return out


def _inject_interactive_widgets(blocks: list[dict], projects: list[dict], *, max_total: int) -> list[dict]:
    """Add mcpSandbox / costSim / chart when absent — zero grounding cost for
    the widgets, real-metrics-derived for chart. Gated by
    ``settings.portfolio_layout.context_enrich_interactive`` (default on).
    """
    enabled = True
    try:
        from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config

        enabled = bool(get_portfolio_layout_config().get("context_enrich_interactive", True))
    except Exception:
        enabled = True
    if not enabled or len(projects) < 2:
        return blocks

    out = list(blocks)
    types = _block_types(out)

    def _insert_before_cta(block: dict) -> None:
        if len(out) >= max_total:
            return
        qa_i = next(
            (i for i, b in enumerate(out) if str(b.get("type") or "") == "quickActions"),
            None,
        )
        if qa_i is None:
            out.append(block)
        else:
            out.insert(qa_i, block)

    if "chart" not in types:
        chart = _chart_from_projects(projects)
        if chart:
            _insert_before_cta(chart)
            types.add("chart")

    if "mcpSandbox" not in types and len(out) < max_total:
        _insert_before_cta({"type": "mcpSandbox", "id": "mcp-sandbox-context", "props": {}})
        types.add("mcpSandbox")

    if "costSim" not in types and len(out) < max_total:
        _insert_before_cta({"type": "costSim", "id": "cost-sim-context", "props": {}})
        types.add("costSim")

    return out


def _dedupe_star_stories(blocks: list[dict]) -> list[dict]:
    """Drop later starStory blocks that repeat the same S/T/A/R fingerprint."""
    seen: set[tuple[str, str, str, str]] = set()
    out: list[dict] = []
    for b in blocks:
        if not isinstance(b, dict) or str(b.get("type") or "") != "starStory":
            out.append(b)
            continue
        props = b.get("props") if isinstance(b.get("props"), dict) else {}
        fp = (
            str(props.get("situation") or "").strip(),
            str(props.get("task") or "").strip(),
            str(props.get("action") or "").strip(),
            str(props.get("result") or "").strip(),
        )
        if fp != ("", "", "", "") and fp in seen:
            logger.debug("enrich: dropping duplicate starStory id=%s", b.get("id"))
            continue
        if fp != ("", "", "", ""):
            seen.add(fp)
        out.append(b)
    return out


def _normalize_body(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _card_bodies(blocks: list[dict]) -> set[str]:
    """Collect normalized card/project body text for dual-summary detection."""
    bodies: set[str] = set()
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = str(b.get("type") or "")
        props = b.get("props") if isinstance(b.get("props"), dict) else {}
        if t == "card":
            for key in ("body", "summary", "description", "subtitle"):
                val = props.get(key)
                if isinstance(val, str) and len(val.strip()) > 40:
                    bodies.add(_normalize_body(val))
        if t == "projectGrid":
            for item in props.get("projects") or props.get("items") or []:
                if not isinstance(item, dict):
                    continue
                for key in ("summary", "body", "description"):
                    val = item.get(key)
                    if isinstance(val, str) and len(val.strip()) > 40:
                        bodies.add(_normalize_body(val))
    return bodies


def _drop_prose_duplicating_cards(blocks: list[dict]) -> list[dict]:
    """Drop deep-dive prose that only re-renders a card's project.summary.

    Stores are context for the agent; card + prose must not double-print the
    same summary string (L2/L6 same-text root cause).
    """
    card_bodies = _card_bodies(blocks)
    if not card_bodies:
        return blocks
    out: list[dict] = []
    for b in blocks:
        if not isinstance(b, dict) or str(b.get("type") or "") != "prose":
            out.append(b)
            continue
        props = b.get("props") if isinstance(b.get("props"), dict) else {}
        md = str(props.get("markdown") or "")
        # Strip a leading markdown heading for comparison.
        body = re.sub(r"^#+\s+[^\n]+\n+", "", md).strip()
        norm = _normalize_body(body)
        if not norm or len(norm) < 40:
            out.append(b)
            continue
        dup = False
        for cb in card_bodies:
            if not cb:
                continue
            # Same text or one contains the other (summary embedded in prose).
            if norm == cb or (len(cb) > 60 and cb in norm) or (len(norm) > 60 and norm in cb):
                dup = True
                break
        if dup:
            logger.debug("enrich: dropping prose that duplicates card summary id=%s", b.get("id"))
            continue
        out.append(b)
    return out


def enrich_layout_blocks(
    blocks: list[dict],
    projects: list[dict] | None,
    *,
    max_total: int = _MAX_TOTAL_BLOCKS,
) -> list[dict]:
    """Add grounded extra blocks and normalize card spans.

    Pure / sync — safe after any compose path. Never invents numeric KPIs.
    """
    if not isinstance(blocks, list):
        return []
    try:
        from plugins.portfolio_plugin.compose.quality import (
            dedupe_layout_cards,
            filter_projects_for_layout,
        )

        projs = filter_projects_for_layout(
            [p for p in (projects or []) if isinstance(p, dict)]
        )
        raw_blocks = [b for b in blocks if isinstance(b, dict)]
        out = _ensure_card_spans(dedupe_layout_cards(raw_blocks, projects=projs))
    except Exception:
        projs = [p for p in (projects or []) if isinstance(p, dict)]
        out = _ensure_card_spans([b for b in blocks if isinstance(b, dict)])
    out = _dedupe_star_stories(out)
    out = _drop_prose_duplicating_cards(out)
    out = _repair_or_drop_empty_shells(out, projs)
    types = _block_types(out)

    # Insert context-driven blocks before quickActions (CTA stays last when present).
    def _insert_before_cta(block: dict) -> None:
        if len(out) >= max_total:
            return
        qa_i = next(
            (i for i, b in enumerate(out) if str(b.get("type") or "") == "quickActions"),
            None,
        )
        if qa_i is None:
            out.append(block)
        else:
            out.insert(qa_i, block)

    if projs and "timeline" not in types:
        tl = _timeline_from_projects(projs)
        if tl:
            _insert_before_cta(tl)
            types.add("timeline")

    if projs and "comparison" not in types and len(projs) >= 2:
        cmp_ = _comparison_from_projects(projs)
        if cmp_:
            _insert_before_cta(cmp_)
            types.add("comparison")

    # Deep-dive prose only when it adds text beyond card bodies (no dual-summary).
    card_bodies = _card_bodies(out)
    if "prose" not in types or sum(1 for b in out if b.get("type") == "prose") < _MAX_EXTRA_PROSE:
        existing_prose = sum(1 for b in out if b.get("type") == "prose")
        budget = _MAX_EXTRA_PROSE - existing_prose
        for i, proj in enumerate(projs):
            if budget <= 0 or len(out) >= max_total:
                break
            prose = _prose_from_project(proj, idx=i)
            if prose is None:
                continue
            # Skip if we already have a prose id for this slug
            if any(b.get("id") == prose["id"] for b in out):
                continue
            # Skip if this would only re-print a card's project.summary body
            sum_norm = _normalize_body(str(proj.get("summary") or ""))
            if sum_norm and len(sum_norm) > 40:
                if any(
                    sum_norm == cb or sum_norm in cb or cb in sum_norm
                    for cb in card_bodies
                    if cb and len(cb) > 40
                ):
                    continue
            _insert_before_cta(prose)
            budget -= 1

    if projs:
        out = _inject_interactive_widgets(out, projs, max_total=max_total)
    # Final pass: drop any prose that still doubles a card body (agent paths).
    out = _drop_prose_duplicating_cards(out)

    return out[:max_total]


async def enrich_layout_dict(
    layout: dict[str, Any] | None,
    *,
    tenant_id: int = 1,
    projects: list[dict] | None = None,
    preserve_dag: bool = False,
) -> dict[str, Any] | None:
    """Enrich a full layout dict and re-stamp DAG + sources.

    When ``preserve_dag=True`` and ``meta.dag`` already exists, band order is
    kept and new block ids are filed into derived bands (patch path). Default
    False fully re-stamps via ``_stamp_dag_from_blocks`` for existing callers.
    """
    if not isinstance(layout, dict):
        return layout
    blocks = layout.get("blocks")
    if not isinstance(blocks, list):
        return layout

    projs = list(projects or [])
    if not projs:
        try:
            from plugins.portfolio_plugin.store import list_projects

            projs = await list_projects(include_inactive=False, tenant_id=int(tenant_id))
        except Exception as exc:
            logger.debug("enrich list_projects fail-open: %s", exc)
            projs = []

    new_blocks = enrich_layout_blocks(blocks, projs)
    # Re-validate and restamp dag
    try:
        from plugins.portfolio_plugin.compose.composer import _stamp_dag_from_blocks
        from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout

        meta = dict(layout.get("meta") or {})
        # Drop internal _sourceRefs before validate; fold into meta.sources
        cleaned: list[dict] = []
        sources = list(meta.get("sources") or []) if isinstance(meta.get("sources"), list) else []
        seen_refs = {
            str(s.get("ref") if isinstance(s, dict) else s)
            for s in sources
        }
        for b in new_blocks:
            b2 = dict(b)
            refs = b2.pop("_sourceRefs", None)
            if isinstance(refs, list):
                for r in refs:
                    rs = str(r).strip()
                    if rs and rs not in seen_refs:
                        seen_refs.add(rs)
                        sources.append({"ref": rs})
            cleaned.append(b2)
        if sources:
            meta["sources"] = sources
        existing_dag = meta.get("dag") if isinstance(meta.get("dag"), dict) else None
        if preserve_dag and existing_dag:
            from plugins.portfolio_plugin.compose.patch import merge_dag_bands

            dag = merge_dag_bands(existing_dag, cleaned)
        else:
            dag = _stamp_dag_from_blocks(cleaned)
        if dag:
            meta["dag"] = dag
        meta["enrichment"] = "project_context"
        candidate = {**layout, "blocks": cleaned, "meta": meta}
        validated, errs = validate_layout(candidate)
        if validated is not None:
            return validated
        logger.debug("enrich validate fail-open: %s", errs)
        return candidate
    except Exception as exc:
        logger.warning("enrich_layout_dict failed open: %s", exc)
        return {**layout, "blocks": new_blocks}
