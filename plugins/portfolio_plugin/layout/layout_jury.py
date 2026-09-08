"""Layout Jury — multi-dimensional critique (Open Design critique theater, lite).

Heuristic scorer (path A) is default for scoped_ask; optional LLM panel (path B)
for redesign/bake when enabled. Returns composite score + must_fix[].
"""

from __future__ import annotations

import logging
import re
from typing import Any

from plugins.portfolio_plugin.themes import SUPPORTED_THEMES, THEME_VOCAB

logger = logging.getLogger("whiskers.plugins.portfolio.layout_jury")

DEFAULT_WEIGHTS = {
    "brief_fit": 0.30,
    "evidence": 0.25,
    "structure": 0.20,
    "schema_craft": 0.15,
    "voice_brand": 0.10,
}

# Content list per block type that can validate as empty (``[]`` schema
# default) but ships as a useless title-only shell. block_builder now rejects
# these on the agent-authored path — this is defense-in-depth so a block that
# reaches the jury already empty (e.g. thin DB-derived data) still costs
# points instead of quietly padding the structure count.
_EMPTY_CONTENT_KEY: dict[str, str] = {
    "comparison": "rows",
    "chart": "series",
    "timeline": "items",
    "kpiGrid": "items",
}

# Widgets/charts a bake/redesign should reach for when there's enough project
# evidence to justify them — see plugins/portfolio_plugin/compose/floor.py's
# _BAND_PLAN (Architecture/Charts bands) for why these cost nothing to include.
_INTERACTIVE_TYPES = frozenset({"mcpSandbox", "costSim"})
_CHART_FAMILY_TYPES = frozenset({"chart", "comparison"})


def _count_empty_shells(blocks: list[dict[str, Any]]) -> int:
    n = 0
    for b in blocks:
        key = _EMPTY_CONTENT_KEY.get(str(b.get("type") or ""))
        if not key:
            continue
        props = b.get("props") if isinstance(b.get("props"), dict) else {}
        val = props.get(key)
        if not isinstance(val, list) or len(val) == 0:
            n += 1
    return n


def _blocks(layout: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(layout, dict):
        return []
    blocks = layout.get("blocks")
    if not isinstance(blocks, list):
        return []
    return [b for b in blocks if isinstance(b, dict)]


def _meta(layout: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(layout, dict):
        return {}
    m = layout.get("meta")
    return m if isinstance(m, dict) else {}


def _score_brief_fit(
    layout: dict[str, Any] | None,
    *,
    query: str,
    goal_class: str,
) -> tuple[float, list[str]]:
    """Heuristic: block types + token overlap between query and block text."""
    blocks = _blocks(layout)
    must: list[str] = []
    if not blocks:
        return 0.0, ["brief_fit: no blocks"]

    # Top keywords only (avoid 700-word JD capping score ~4/10)
    raw_tokens = [t for t in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(t) > 3]
    # de-dupe preserve order, keep top ~25
    seen: set[str] = set()
    q_tokens: list[str] = []
    for t in raw_tokens:
        if t in seen:
            continue
        seen.add(t)
        q_tokens.append(t)
        if len(q_tokens) >= 25:
            break
    q_set = set(q_tokens)

    def _walk_props(obj: Any, parts: list[str], depth: int = 0) -> None:
        if depth > 4 or obj is None:
            return
        if isinstance(obj, str):
            if len(obj) > 2:
                parts.append(obj)
            return
        if isinstance(obj, dict):
            for v in obj.values():
                _walk_props(v, parts, depth + 1)
        elif isinstance(obj, list):
            for v in obj[:20]:
                _walk_props(v, parts, depth + 1)

    blob_parts: list[str] = []
    for b in blocks:
        props = b.get("props") if isinstance(b.get("props"), dict) else {}
        _walk_props(props, blob_parts)
        blob_parts.append(str(b.get("type") or ""))
    blob = " ".join(blob_parts).lower()
    if not q_set:
        score = 7.0 if goal_class in ("redesign", "bake_for_job") else 6.5
        return score, must

    hits = sum(1 for t in q_set if t in blob)
    ratio = hits / max(len(q_set), 1)
    score = 4.0 + min(6.0, ratio * 10.0)
    if ratio < 0.15 and len(q_set) >= 3:
        must.append("brief_fit: layout text barely addresses query terms — re-plan angle blocks")
        score = min(score, 4.5)
    return min(10.0, score), must


def _score_evidence(layout: dict[str, Any] | None) -> tuple[float, list[str]]:
    blocks = _blocks(layout)
    meta = _meta(layout)
    must: list[str] = []
    if not blocks:
        return 0.0, ["evidence: no blocks"]

    sources = meta.get("sources") or []
    n_sources = len(sources) if isinstance(sources, list) else 0
    authored_types = {"prose", "chart", "comparison", "codeSnippet", "composite"}
    authored = [b for b in blocks if str(b.get("type") or "") in authored_types]
    cited = 0
    for b in authored:
        refs = b.get("_sourceRefs") or b.get("source_refs")
        if isinstance(refs, list) and refs:
            cited += 1
        # Also accept meta.sources presence as weak evidence for authored
        elif n_sources > 0:
            cited += 0.5  # type: ignore[assignment]

    if not authored:
        # DB-derived only: sources on meta is good enough
        score = 8.0 if n_sources > 0 or len(blocks) >= 3 else 6.0
        return score, must

    ratio = float(cited) / max(len(authored), 1)
    score = 3.0 + min(7.0, ratio * 7.0)
    if ratio < 0.5:
        must.append("evidence: authored blocks missing grounded source_refs")
    return min(10.0, score), must


def _score_structure(
    layout: dict[str, Any] | None,
    *,
    goal_class: str,
    quality: dict[str, Any] | None = None,
) -> tuple[float, list[str]]:
    from plugins.portfolio_plugin.compose.recipes import min_blocks_for

    blocks = _blocks(layout)
    meta = _meta(layout)
    must: list[str] = []
    if not blocks:
        return 0.0, ["structure: blocks empty"]

    types = {str(b.get("type") or "") for b in blocks}
    types.discard("")
    need = min_blocks_for(goal_class)
    if quality and isinstance(quality.get("min_blocks"), int):
        need = max(need, int(quality["min_blocks"]))

    score = 5.0
    if len(blocks) >= need:
        score += 2.0
        # Richness gradient with headroom so band-crowding penalties stay visible
        extra = min(1.0, 0.15 * max(0, len(blocks) - need))
        score = min(9.2, score + extra)
    else:
        must.append(f"structure: need ≥{need} blocks, got {len(blocks)}")
        score -= 1.5

    min_types = 3 if goal_class in ("redesign", "bake_for_job") else 2
    if quality and isinstance(quality.get("min_types"), int):
        # max(), not overwrite: quality is now always populated (merge_quality_floor
        # defaults min_types=2 even with no recipe) — an overwrite would let an
        # unmatched-recipe redesign silently drop its 3-type floor to 2.
        min_types = max(min_types, int(quality["min_types"]))
    if len(types) >= min_types:
        score += 1.5
    else:
        must.append(f"structure: need ≥{min_types} block types, got {len(types)}")

    dag = meta.get("dag")
    require_dag = bool((quality or {}).get("require_dag")) or goal_class in (
        "redesign",
        "bake_for_job",
    )
    if require_dag:
        if isinstance(dag, dict) and (dag.get("levels") or dag.get("nodes")):
            score += 1.5
        else:
            must.append("structure: full-page layouts should stamp meta.dag levels")
            score -= 0.5
    else:
        score += 0.5

    # Reward real composition (multiple bands, no single band hoarding most of
    # the page) over a flat type->band stamp -- gives the agent a gradient to
    # climb toward deliberate structure once plan-driven bands (Phase 2.1)
    # let it choose placement instead of inheriting the type->level default.
    dag_levels = dag.get("levels") if isinstance(dag, dict) else None
    if isinstance(dag_levels, list) and dag_levels:
        band_sizes = [len(lvl.get("nodes") or []) for lvl in dag_levels if isinstance(lvl, dict)]
        total_banded = sum(band_sizes)
        if total_banded > 0:
            if max(band_sizes) / total_banded > 0.6:
                must.append("structure: most blocks crowded into a single matrix band")
                score -= 0.5
            elif len(dag_levels) >= 3:
                score = min(10.0, score + 0.5)

    # Small reward for shipping a genuinely visual block (Phase 6) so "more
    # live" is something the agent is scored on, not merely permitted.
    if _has_visual_block(blocks):
        score = min(10.0, score + 0.3)

    # Free-structure incentive: cloning the job-bake recipe type sequence
    # is a structure fail when meta.structureMode=free (or bake/redesign).
    meta_mode = str(meta.get("structureMode") or "").strip().lower()
    if meta_mode == "free" or goal_class in ("bake_for_job", "redesign"):
        try:
            from plugins.portfolio_plugin.compose.floor import FLOOR_CLONE_TYPES
            from plugins.portfolio_plugin.layout.evidence_pack import is_floor_clone

            if is_floor_clone({"blocks": blocks}, FLOOR_CLONE_TYPES):
                must.append(
                    "structure: type sequence clones floor composer — restructure "
                    "from catalog + evidence (do not replay deterministic default)"
                )
                score = min(score, 5.5)
                score -= 1.0
            elif meta.get("structureClone"):
                must.append("structure: plan flagged structureClone — restructure")
                score = min(score, 6.0)
        except Exception:
            logger.debug("layout_jury.py: swallowed exception", exc_info=True)

    # Evidence coverage: when inventory is small, prefer citing multiple projects
    scoped = meta.get("scopedProjectCount")
    try:
        scoped_n = int(scoped) if scoped is not None else 0
    except (TypeError, ValueError):
        scoped_n = 0
    if goal_class == "bake_for_job" and scoped_n >= 3:
        score = min(10.0, score + 0.3)

    # Empty-content shells (e.g. a title-only comparison table) are a
    # structure fail, not free richness — see the bake-parity Playwright
    # finding this whole gate exists for.
    empty_n = _count_empty_shells(blocks)
    if empty_n:
        must.append(
            f"structure: {empty_n} block(s) ship empty content "
            "(comparison.rows / chart.series / timeline.items / kpiGrid.items) — "
            "fill from real project data or drop the block"
        )
        score -= 1.0 * empty_n

    # Catalog-coverage reward: bake/redesign pages should reach for at least
    # one zero-cost interactive widget and one grounded chart-family block
    # once there's enough evidence to justify a full page (mirrors the
    # default CatPortfolio page's Architecture/Charts bands).
    if goal_class in ("bake_for_job", "redesign"):
        has_interactive = bool(types & _INTERACTIVE_TYPES)
        has_chart_family = bool(types & _CHART_FAMILY_TYPES)
        if has_interactive and has_chart_family:
            score = min(10.0, score + 0.5)
        elif scoped_n >= 3:
            must.append(
                "structure: no interactive widget (mcpSandbox/costSim) or "
                "chart/comparison block — catalog has zero-cost widgets, use them"
            )

    return max(0.0, min(10.0, score)), must


def _has_visual_block(blocks: list[dict[str, Any]]) -> bool:
    """scene2d (Phase 6b, always visual) or archDiagram rendered as a
    generated SVG motif (Phase 6a, kind="svg") rather than a static mermaid
    flowchart."""
    for b in blocks:
        btype = b.get("type")
        if btype == "scene2d":
            return True
        if btype == "archDiagram" and (b.get("props") or {}).get("kind") == "svg":
            return True
    return False


def _score_schema_craft(layout: dict[str, Any] | None) -> tuple[float, list[str]]:
    from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout

    must: list[str] = []
    if not isinstance(layout, dict):
        return 0.0, ["schema_craft: layout missing"]

    meta = _meta(layout)
    if str(meta.get("mode") or "") == "template":
        must.append("schema_craft: template-mode fallback forbidden for agentic path")
        return 2.0, must

    _, errs = validate_layout(layout)
    if errs:
        must.append(f"schema_craft: validation errors: {errs[:3]}")
        return 3.0, must
    return 9.0, must


def _score_voice_brand(layout: dict[str, Any] | None, *, theme: str = "") -> tuple[float, list[str]]:
    meta = _meta(layout)
    must: list[str] = []
    t = str(meta.get("theme") or theme or "").strip().lower()
    if t in SUPPORTED_THEMES:
        score = 8.0
    elif t:
        score = 6.0
        must.append(f"voice_brand: unusual theme '{t}' — prefer {THEME_VOCAB}")
    else:
        score = 5.5
    if isinstance(meta.get("themeOverrides"), dict) and meta["themeOverrides"]:
        score = min(10.0, score + 1.0)
    return score, must


def score_layout_heuristic(
    layout: dict[str, Any] | None,
    *,
    query: str = "",
    goal_class: str = "scoped_ask",
    theme: str = "",
    quality: dict[str, Any] | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Multi-dim heuristic jury. Returns scores, composite, must_fix, pass."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    dims: dict[str, float] = {}
    must_fix: list[str] = []

    s, m = _score_brief_fit(layout, query=query, goal_class=goal_class)
    dims["brief_fit"] = s
    must_fix.extend(m)

    s, m = _score_evidence(layout)
    dims["evidence"] = s
    must_fix.extend(m)

    s, m = _score_structure(layout, goal_class=goal_class, quality=quality)
    dims["structure"] = s
    must_fix.extend(m)

    s, m = _score_schema_craft(layout)
    dims["schema_craft"] = s
    must_fix.extend(m)

    s, m = _score_voice_brand(layout, theme=theme)
    dims["voice_brand"] = s
    must_fix.extend(m)

    composite = sum(dims[k] * w.get(k, 0.0) for k in dims)
    # Cap 0-10
    composite = max(0.0, min(10.0, round(composite, 2)))
    return {
        "status": "ok",
        "mode": "heuristic",
        "dimensions": dims,
        "weights": w,
        "composite": composite,
        "must_fix": must_fix,
        "block_count": len(_blocks(layout)),
    }


async def critique_layout(
    layout: dict[str, Any] | None,
    *,
    query: str = "",
    goal_class: str = "scoped_ask",
    theme: str = "",
    quality: dict[str, Any] | None = None,
    threshold: float | None = None,
    use_llm: bool | None = None,
) -> dict[str, Any]:
    """Run layout jury; optional LLM panel when use_llm and LLM available.

    Always runs heuristic first; LLM (path B) can refine brief_fit only when
    enabled — full multi-panelist theater is out of scope for v1.
    """
    from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config

    cfg = get_portfolio_layout_config()
    thr = float(threshold if threshold is not None else cfg.get("jury_threshold", 7.5))
    want_llm = use_llm if use_llm is not None else (
        goal_class in ("redesign", "bake_for_job") and bool(cfg.get("jury_use_llm", False))
    )

    result = score_layout_heuristic(
        layout,
        query=query,
        goal_class=goal_class,
        theme=theme,
        quality=quality,
    )

    if want_llm:
        try:
            llm_delta = await _llm_brief_fit_panel(layout, query=query, goal_class=goal_class)
            if llm_delta is not None:
                dims = dict(result["dimensions"])
                dims["brief_fit"] = llm_delta
                w = result["weights"]
                composite = sum(dims[k] * w.get(k, 0.0) for k in dims)
                result = {
                    **result,
                    "mode": "heuristic+llm",
                    "dimensions": dims,
                    "composite": max(0.0, min(10.0, round(composite, 2))),
                }
        except Exception as exc:
            logger.debug("layout jury LLM panel skipped: %s", exc)

    passed = float(result["composite"]) >= thr and not any(
        m.startswith("schema_craft: validation") for m in result.get("must_fix") or []
    )
    # Template mode hard-fail
    if str(_meta(layout).get("mode") or "") == "template" and goal_class in (
        "redesign",
        "bake_for_job",
    ):
        passed = False
        if "schema_craft: template-mode fallback forbidden for agentic path" not in (
            result.get("must_fix") or []
        ):
            result.setdefault("must_fix", []).append(
                "schema_craft: template-mode fallback forbidden for agentic path"
            )

    result["threshold"] = thr
    result["passed"] = passed
    result["ship_best"] = not passed  # caller may ship best after max rounds
    return result


async def _llm_brief_fit_panel(
    layout: dict[str, Any] | None,
    *,
    query: str,
    goal_class: str,
) -> float | None:
    """Single-call brief_fit score 0-10; None if unavailable."""
    try:
        from core.llm_provider_management import llm_available

        if not llm_available():
            return None
        from core.llm_config_service import get_graph_core_llm

        llm = await get_graph_core_llm()
        if llm is None:
            return None
    except Exception:
        return None

    blocks = _blocks(layout)
    types = [str(b.get("type") or "") for b in blocks[:12]]
    prompt = (
        "Score 0-10 how well this portfolio GenUI layout addresses the brief. "
        "Reply with ONLY a number.\n"
        f"goal_class={goal_class}\n"
        f"brief={query[:800]}\n"
        f"block_types={types}\n"
    )
    try:
        msg = await llm.ainvoke(prompt)
        text = getattr(msg, "content", None) or str(msg)
        if isinstance(text, list):
            text = " ".join(
                str(p.get("text") if isinstance(p, dict) else p) for p in text
            )
        import re

        m = re.search(r"(\d+(?:\.\d+)?)", str(text))
        if not m:
            return None
        return max(0.0, min(10.0, float(m.group(1))))
    except Exception as exc:
        logger.debug("llm brief_fit failed: %s", exc)
        return None
