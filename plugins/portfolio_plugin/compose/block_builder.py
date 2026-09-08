"""Per-block, scoped, grounded layout block builder.

Lets a GOAP agent build ONE validated layout block at a time, scoped to a
subset of projects/context (``slugs`` / ``query``) instead of always rendering
the whole portfolio inventory. Reuses the existing composer/fragment builders
— no duplicated composition logic.

Two families of block types:

- **DB/context-derived** (``hero``, ``statStrip``, ``projectGrid``, ``kpiGrid``,
  ``timeline``, ``starStory``, ``archDiagram``): the builder selects its own
  scoped content (by explicit ``slugs`` or by semantic ``query``) and derives
  ``source_refs`` automatically from the projects it used — the agent never
  has to cite these.
- **Agent-authored** (``prose``, ``chart``, ``comparison``, ``codeSnippet``,
  and ``archDiagram`` with a real ``source``): the agent supplies ``props``
  directly. ``source_refs`` are REQUIRED and are verified against real
  indexed docs / declared project sources before the block is accepted —
  never trusted blindly (anti prompt-injection / anti-fabrication guard).
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from plugins.portfolio_plugin.store import list_projects
from plugins.portfolio_plugin.compose import composer
from plugins.portfolio_plugin.discovery.index import search_context
from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout

logger = logging.getLogger("whiskers.plugins.portfolio")


def _short_label(name: str, max_len: int) -> str:
    """Diagram node label: drop an owner/org prefix before truncating.

    Discovered project names are often ``Owner/Repo`` (see normalize.py);
    a flat char-slice truncates every repo under one GitHub org to the same
    prefix (e.g. all "CooLguNxDD/..." nodes rendering as identical text).
    Preferring the repo segment, then adding an ellipsis only when the
    remainder still doesn't fit, keeps diagram nodes visually distinct.
    """
    name = str(name or "").strip()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    if len(name) > max_len:
        return name[: max(1, max_len - 1)].rstrip() + "…"
    return name

# Types the builder resolves its own DB/context-derived content for.
# Interactive shells (mcpSandbox / costSim / quickActions) are client widgets
# with empty or settings-derived props — agents may choose them dynamically.
# chart is dual: metrics-derived when props lack series; props win when full.
_DB_DERIVED_TYPES = frozenset(
    {
        "hero",
        "statStrip",
        "projectGrid",
        "kpiGrid",
        "timeline",
        "starStory",
        "flowAnim",
        "card",  # domain-tinted matrix cards from ranked projects
        "chart",
        "mcpSandbox",
        "costSim",
        "quickActions",
        "scene2d",  # canvas 2D visual, grounded from real projects (Phase 6b)
        "fishTank",  # WebGL aquarium, grounded from real projects
    }
)
_SCENE2D_PRESETS = ("orbit", "pulse-grid", "particle-field")
# Types where the agent supplies props directly and must cite sources.
_AUTHORED_TYPES = frozenset({
    "prose", "comparison", "codeSnippet", "composite",
})
# archDiagram is DB-derived when thin (title/query only) and agent-authored
# when a real ``source`` is supplied (mirrors composer._is_incomplete_arch_diagram).
_ARCH_TYPE = "archDiagram"

SUPPORTED_BLOCK_TYPES = frozenset(_DB_DERIVED_TYPES | _AUTHORED_TYPES | {_ARCH_TYPE})


def _normalize_layout_hint(layout: dict | None) -> dict | None:
    """Accept ``{span?, order?}`` for the 12-col LayoutRenderer grid."""
    if not layout or not isinstance(layout, dict):
        return None
    out: dict[str, int] = {}
    span = layout.get("span")
    if span is not None:
        try:
            s = int(span)
        except (TypeError, ValueError):
            return None  # caller surfaces schema error if invalid attached later
        if 1 <= s <= 12:
            out["span"] = s
    order = layout.get("order")
    if order is not None:
        try:
            out["order"] = int(order)
        except (TypeError, ValueError):
            pass
    return out or None


def _apply_layout_hint(block: dict | None, layout: dict | None) -> dict | None:
    """Stamp layout.span/order onto a block dict before validation.

    Merges key-wise over any hint the builder already set (e.g. cards
    default ``span=6``) so a partial order-only hint does not wipe span.
    """
    if block is None:
        return None
    hint = _normalize_layout_hint(layout)
    if not hint:
        return block
    out = dict(block)
    existing = out.get("layout") if isinstance(out.get("layout"), dict) else {}
    out["layout"] = {**existing, **hint}
    return out


def _virtual_project_from_hit(hit: dict) -> dict | None:
    """Synthesize a project-shaped dict from an indexed ``portfolio_plugin__context`` hit.

    Lets DB-derived blocks render real discovered content even before a
    ``PortfolioProject`` row exists for it (discovery write-back is async /
    may not have run yet) — otherwise "no project row" silently means "no
    dynamic layout" no matter how much context is indexed.
    """
    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    ref = str(meta.get("ref") or "").strip()
    slug = str(meta.get("slug_hint") or "").strip()
    title = str(meta.get("title") or ref or slug or "Untitled project").strip()
    if not slug and not ref:
        return None
    text = hit.get("content_text") or ""
    summary = ""
    for line in str(text).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            summary = line[:500]
            break
    links = []
    url = meta.get("url")
    kind = str(meta.get("kind") or "github")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        links.append({"label": kind, "href": url})
    tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
    return {
        "slug": slug or ref,
        "name": title,
        "summary": summary or f"Discovered {kind} project.",
        "tags": tags,
        "metrics": [],
        "links": links,
        "context_sources": [{"id": f"disc:{kind}:{ref}", "kind": kind, "ref": ref}] if ref else [],
        "virtual": True,
    }


async def _virtual_projects_for_query(
    *, tenant_id: int, query: str, top_k: int
) -> list[dict]:
    """Fall back to indexed context docs when no PortfolioProject rows match."""
    if not query or not str(query).strip():
        return []
    try:
        hits = await search_context(str(query), tenant_id=tenant_id, top_k=max(top_k * 2, 12))
    except Exception as exc:
        logger.debug("virtual project fallback: search failed: %s", exc)
        return []
    out: list[dict] = []
    seen_slugs: set[str] = set()
    for h in hits:
        proj = _virtual_project_from_hit(h)
        if proj is None:
            continue
        key = str(proj.get("slug") or "").lower()
        if key in seen_slugs:
            continue
        seen_slugs.add(key)
        out.append(proj)
        if len(out) >= top_k:
            break
    return out


def _merge_project_lists(
    primary: list[dict],
    secondary: list[dict],
    *,
    top_k: int | None,
) -> list[dict]:
    """Prefer primary (DB) rows; append secondary (index virtual) by unique slug."""
    out: list[dict] = []
    seen: set[str] = set()
    for bag in (primary, secondary):
        for p in bag or []:
            if not isinstance(p, dict):
                continue
            slug = str(p.get("slug") or "").lower()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            out.append(p)
            if top_k and len(out) >= top_k:
                return out
    return out


async def _resolve_projects(
    *,
    tenant_id: int,
    slugs: list[str] | None,
    query: str,
    top_k: int,
) -> list[dict]:
    """Resolve projects from **DB inventory + discovery index**, not a fixed list.

    Explicit slugs win (DB first, then virtual index). Query path ranks the full
    ``portfolio_projects`` table and **always merges** high-scoring virtual
    projects synthesized from ``portfolio_plugin__context`` so materialization
    can surface discovered repos that are not yet written back as rows.
    Empty slugs + empty query returns the full active inventory.
    """
    from plugins.portfolio_plugin.compose.quality import (
        filter_projects_for_layout,
        is_portfolio_worthy_project,
        is_stub_summary,
    )

    all_projects = await list_projects(tenant_id=tenant_id)
    # Materialize prefers worthy rows; explicit slug lookups still resolve.
    worthy = filter_projects_for_layout(all_projects)

    if slugs:
        wanted = {str(s).strip().lower() for s in slugs if str(s).strip()}
        by_slug = {str(p.get("slug") or "").lower(): p for p in all_projects}
        matched = [by_slug[s] for s in wanted if s in by_slug]
        if len(matched) > 1 and query and str(query).strip():
            matched = await composer.rank_projects_by_query(
                matched, query, tenant_id=tenant_id, top_k=len(matched)
            )
        virtual = await _virtual_projects_for_query(
            tenant_id=tenant_id,
            query=" ".join(wanted) + (" " + query if query else ""),
            top_k=top_k or len(wanted) or 6,
        )
        virtual = [
            v
            for v in virtual
            if isinstance(v, dict)
            and not is_stub_summary(str(v.get("summary") or ""))
            and is_portfolio_worthy_project(v)
        ]
        if matched:
            return _merge_project_lists(matched, virtual, top_k=top_k or None)
        return virtual[: top_k or len(virtual)]

    if query and str(query).strip():
        ranked = await composer.rank_projects_by_query(
            worthy or all_projects,
            query,
            tenant_id=tenant_id,
            top_k=max(top_k * 2, 12),
        )
        virtual = await _virtual_projects_for_query(
            tenant_id=tenant_id, query=query, top_k=max(top_k or 3, 6)
        )
        virtual = [
            v
            for v in virtual
            if isinstance(v, dict)
            and not is_stub_summary(str(v.get("summary") or ""))
            and is_portfolio_worthy_project(v)
        ]
        return _merge_project_lists(
            ranked or [],
            virtual,
            top_k=top_k if top_k else None,
        )

    # No query: worthy inventory only (not school-lab stubs).
    bag = worthy or list(all_projects)
    return list(bag) if not top_k else list(bag)[:top_k]


def _source_refs_from_projects(projects: list[dict]) -> list[str]:
    """Derive citations from each project's declared context_sources — no extra query."""
    refs: list[str] = []
    seen: set[str] = set()
    for p in projects:
        for src in p.get("context_sources") or []:
            if not isinstance(src, dict):
                continue
            ref = str(src.get("id") or src.get("ref") or "").strip()
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)
    return refs


def _star_props_from_dict(props: dict | None) -> dict[str, Any] | None:
    """Extract grounded STAR fields from plan/authored props (no portfolio_star)."""
    if not isinstance(props, dict):
        return None
    situation = str(props.get("situation") or "").strip()
    task = str(props.get("task") or "").strip()
    action = str(props.get("action") or "").strip()
    result = str(props.get("result") or "").strip()
    if not (situation and task and action and result):
        return None
    tags = props.get("tags") if isinstance(props.get("tags"), list) else []
    return {
        "situation": situation,
        "task": task,
        "action": action,
        "result": result,
        "tags": [str(t) for t in tags[:8] if t],
    }


async def _resolve_star_from_context(
    *, tenant_id: int, query: str, top_k: int
) -> list[dict]:
    """Best-effort STAR-shaped rows from portfolio_plugin__context (not portfolio_star).

    Only returns hits that already carry structured situation/task/action/result
    metadata (e.g. discovery docs that encoded STAR). Never fabricates S/T/A/R.
    """
    if not query or not str(query).strip():
        return []
    try:
        hits = await search_context(
            str(query), tenant_id=tenant_id, top_k=max(int(top_k) * 3, 8)
        )
    except Exception as exc:
        logger.warning("build_layout_block: context STAR search fail-open: %s", exc)
        return []
    out: list[dict] = []
    for h in hits or []:
        if not isinstance(h, dict):
            continue
        meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
        if not (
            meta.get("situation")
            and meta.get("task")
            and meta.get("action")
            and meta.get("result")
        ):
            continue
        out.append(h)
        if len(out) >= max(1, int(top_k)):
            break
    return out


def _dump(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True, by_alias=True)
    return block if isinstance(block, dict) else {}


async def _build_db_derived(
    block_type: str,
    *,
    tenant_id: int,
    query: str,
    slugs: list[str] | None,
    top_k: int,
    block_id: str,
    props: dict | None,
) -> tuple[dict | None, list[str], list[str]]:
    """Returns (block_dict_or_None, source_refs, errors)."""
    from plugins.portfolio_plugin.plugin_config import SETTINGS

    if block_type == "hero":
        block = composer._hero(SETTINGS.get("hero"))
        if block_id:
            block = block.model_copy(update={"id": block_id})
        return _dump(block), [], []

    if block_type == "starStory":
        # Prefer LLM-authored props (context-first bake). Fall back to context
        # corpus rows that already carry STAR metadata — never portfolio_star.
        authored = _star_props_from_dict(props)
        if authored:
            from plugins.portfolio_plugin.schema.ui_layout_schema import (
                StarStoryBlock,
                StarStoryProps,
            )

            blk = StarStoryBlock(
                id=block_id or "star-1",
                props=StarStoryProps(**authored),
            )
            refs = [
                str(r)
                for r in (props.get("source_refs") or props.get("sourceRefs") or [])
                if isinstance(props, dict) and str(r).strip()
            ] if isinstance(props, dict) else []
            return _dump(blk), refs, []

        # top_k for starStory is 1-based rank when small (1..5). The generic
        # build_layout_block default (6) is a project-count default — treat as rank 1.
        rank = int(top_k or 1)
        if rank > 5:
            rank = 1
        want = max(1, rank)
        stories = await _resolve_star_from_context(
            tenant_id=tenant_id, query=query, top_k=max(want, 3)
        )
        built = composer._star_stories(stories)
        if not built:
            return None, [], [
                "starStory: author props {situation,task,action,result} from "
                "evidence pack, or index STAR-shaped context docs — "
                "portfolio_star is retired"
            ]
        if want > len(built):
            return None, [], [
                f"starStory: only {len(built)} context STAR hit(s); cannot fill rank {want}"
            ]
        blk = built[want - 1]
        if block_id:
            blk = blk.model_copy(update={"id": block_id})
        # Cite context refs when present
        refs: list[str] = []
        if want - 1 < len(stories):
            meta = stories[want - 1].get("metadata") or {}
            if isinstance(meta, dict) and meta.get("ref"):
                refs.append(str(meta["ref"]))
        return _dump(blk), refs, []

    projects = await _resolve_projects(tenant_id=tenant_id, slugs=slugs, query=query, top_k=top_k)
    source_refs = _source_refs_from_projects(projects)

    if block_type == "statStrip":
        blk = composer._stat_strip(projects)
        if blk is None:
            return None, [], ["statStrip: no headline metrics available for the selected projects"]
        if block_id:
            blk = blk.model_copy(update={"id": block_id})
        return _dump(blk), source_refs, []

    if block_type == "projectGrid":
        blk = composer._project_grid(projects)
        if blk is None:
            return None, [], ["projectGrid: no projects matched the given slugs/query"]
        if block_id:
            blk = blk.model_copy(update={"id": block_id})
        return _dump(blk), source_refs, []

    if block_type == "card":
        cards = composer._project_cards(projects)
        if not cards:
            return None, [], ["card: no projects matched the given slugs/query"]
        dumped = [_dump(c) for c in cards]
        if block_id and dumped:
            dumped[0] = {**dumped[0], "id": block_id}
        # Always return a list so callers can expand multi-card matrices.
        return dumped, source_refs, []

    if block_type == "kpiGrid":
        # local _build_proof_kpi

        out = _build_proof_kpi({"projects": projects}, {})
        if not out:
            return None, [], ["kpiGrid: no metrics available for the selected projects"]
        blk = out[0]
        if block_id:
            blk["id"] = block_id
        return blk, source_refs, []

    if block_type == "timeline":
        # local _build_proof_timeline

        out = _build_proof_timeline({"projects": projects}, {})
        if not out:
            return None, [], ["timeline: no projects available for the selected slugs/query"]
        blk = out[0]
        if block_id:
            blk["id"] = block_id
        return blk, source_refs, []

    if block_type == "flowAnim":
        nodes = [{"id": "root", "label": "Agent", "group": "core"}]
        edges = []
        for i, p in enumerate(projects[:5]):
            nid = f"p{i}"
            nodes.append({"id": nid, "label": _short_label(p.get("name") or p.get("slug") or nid, 16)})
            edges.append({"from": "root", "to": nid})
        if len(nodes) == 1:
            nodes.extend([
                {"id": "mcp", "label": "MCP Router"},
                {"id": "goap", "label": "GOAP Orchestrator"},
            ])
            edges.extend([{"from": "root", "to": "mcp"}, {"from": "mcp", "to": "goap"}])
        blk = {
            "type": "flowAnim",
            "id": block_id or "sys-flow",
            "props": {"title": "Agent System Flow", "nodes": nodes, "edges": edges, "animate": True},
        }
        return blk, source_refs, []

    if block_type == "scene2d":
        # Grounded like flowAnim: nodes/edges are derived from real projects,
        # never agent-invented -- the agent only picks preset/palette/motion.
        # renderer is fixed "2d" (schema union left open for "webgl" later --
        # a widening, not a rewrite, once that dependency is worth adding).
        p = props if isinstance(props, dict) else {}
        preset = str(p.get("preset") or "orbit").strip()
        if preset not in _SCENE2D_PRESETS:
            preset = "orbit"
        palette = p.get("palette") if isinstance(p.get("palette"), str) else None
        motion = p.get("motion") if isinstance(p.get("motion"), dict) else {}

        nodes = [{"id": "root", "label": (SETTINGS.get("hero") or {}).get("name") or "Portfolio", "group": "core"}]
        edges = []
        for i, proj in enumerate(projects[:6]):
            nid = f"p{i}"
            nodes.append({"id": nid, "label": _short_label(proj.get("name") or proj.get("slug") or nid, 20)})
            edges.append({"from": "root", "to": nid})
        if len(nodes) == 1:
            nodes.extend([{"id": "mcp", "label": "MCP Router"}, {"id": "goap", "label": "GOAP Orchestrator"}])
            edges.extend([{"from": "root", "to": "mcp"}, {"from": "mcp", "to": "goap"}])

        block_props: dict[str, Any] = {
            "renderer": "2d",
            "preset": preset,
            "nodes": nodes,
            "edges": edges,
            "motion": motion,
        }
        title = p.get("title") or p.get("query") or p.get("caption")
        if title:
            block_props["title"] = str(title)
        if palette:
            block_props["palette"] = palette
        caption = p.get("caption")
        if caption and caption != title:
            block_props["caption"] = str(caption)

        blk = {"type": "scene2d", "id": block_id or "scene2d-1", "props": block_props}
        return blk, source_refs, []

    if block_type == "fishTank":
        # Grounded specimens from resolved projects (same inventory as cards).
        from plugins.portfolio_plugin.compose.fish import build_fish_tank_block

        p = props if isinstance(props, dict) else {}
        hl = p.get("highlightSlugs") if isinstance(p.get("highlightSlugs"), list) else None
        # A caller-supplied timeSpan freezes the depth scale (ask-mode patches
        # pass the tank's current span so one new fish can't re-depth the school).
        tank = build_fish_tank_block(
            projects,
            highlight_slugs=[str(s) for s in (hl or []) if s],
            block_id=block_id or "fish-tank-1",
            title=str(p.get("title") or "") or None,
            curation_label=str(p.get("curationLabel") or "") or None,
            tank_theme=str(p.get("tankTheme") or "") or None,
            time_span=p.get("timeSpan") if isinstance(p.get("timeSpan"), dict) else None,
        )
        if not tank:
            return None, source_refs, ["fishTank: no projects resolved to specimens"]
        return tank, source_refs, []

    if block_type == "chart":
        # Prefer agent-authored series when provided; else derive from project metrics.
        if isinstance(props, dict) and isinstance(props.get("series"), list) and props["series"]:
            pcopy = dict(props)
            pcopy.setdefault("kind", pcopy.get("chartKind") or "bar")
            pcopy.setdefault("title", "Metrics")
            return {
                "type": "chart",
                "id": block_id or "chart-1",
                "props": pcopy,
            }, source_refs, []
        import re
        pts = []
        for p in projects:
            for m in p.get("metrics") or []:
                if isinstance(m, dict) and m.get("label") and m.get("value"):
                    try:
                        v_str = str(m["value"])
                        nums = re.findall(r"\d+\.?\d*", v_str)
                        val = float(nums[0]) if nums else 10.0
                        pts.append({"x": str(m["label"])[:14], "y": val})
                    except Exception:
                        logger.debug("block_builder.py: swallowed exception", exc_info=True)
                if len(pts) >= 5:
                    break
            if len(pts) >= 5:
                break
        if not pts:
            pts = [
                {"x": "Raw Surface", "y": 144.0},
                {"x": "Vector Filtered", "y": 3.0},
                {"x": "Active Plugins", "y": 5.0},
            ]
        blk = {
            "type": "chart",
            "id": block_id or "chart-1",
            "props": {
                "title": "Context & Benchmark Performance",
                "kind": "bar",
                "chartKind": "bar",
                "series": [{"name": "metrics", "points": pts}],
            },
        }
        return blk, source_refs, []

    if block_type == "mcpSandbox":
        return {
            "type": "mcpSandbox",
            "id": block_id or "mcp-sandbox",
            "props": dict(props) if isinstance(props, dict) else {},
        }, [], []

    if block_type == "costSim":
        return {
            "type": "costSim",
            "id": block_id or "cost-sim",
            "props": dict(props) if isinstance(props, dict) else {},
        }, [], []

    if block_type == "quickActions":
        # local _build_cta_quick_actions

        out = _build_cta_quick_actions({"projects": projects}, props if isinstance(props, dict) else {})
        if not out:
            return None, [], ["quickActions: no actions configured"]
        blk = out[0]
        if block_id:
            blk["id"] = block_id
        if isinstance(props, dict) and props.get("prompt"):
            p = dict(blk.get("props") or {})
            p["prompt"] = str(props["prompt"])
            blk["props"] = p
        return blk, source_refs, []

    return None, [], [f"block_type: unsupported '{block_type}'"]


async def _build_arch_diagram(
    *,
    tenant_id: int,
    query: str,
    slugs: list[str] | None,
    top_k: int,
    block_id: str,
    props: dict | None,
) -> tuple[dict | None, list[str], list[str]]:
    props = props or {}
    thin = not (props.get("source") and str(props.get("source")).strip())
    if thin:
        projects = await _resolve_projects(tenant_id=tenant_id, slugs=slugs, query=query, top_k=top_k)
        sec = {"id": block_id or None, "props": dict(props)}
        blk = composer._enrich_arch_diagram(sec, projects)
        return blk, _source_refs_from_projects(projects), []
    # Real source supplied by the agent — treat as authored, require citations.
    return None, [], []  # signals caller to fall through to authored path


async def _verify_source_refs(
    *,
    tenant_id: int,
    query: str,
    source_refs: list[str],
    top_k: int,
) -> list[str]:
    """Return the subset of source_refs that could NOT be grounded."""
    if not source_refs:
        return ["source_refs"]

    # Known project-declared sources (any project, not just scoped ones) are
    # always legitimate citations.
    known: set[str] = set()
    try:
        for p in await list_projects(tenant_id=tenant_id):
            for src in p.get("context_sources") or []:
                if isinstance(src, dict):
                    for v in (src.get("id"), src.get("ref")):
                        if v:
                            known.add(str(v).strip().lower())
    except Exception as exc:
        logger.debug("build_layout_block: known-sources lookup failed: %s", exc)

    hit_texts: list[str] = []
    seed_query = query if query and str(query).strip() else " ".join(source_refs)
    try:
        hits = await search_context(seed_query, tenant_id=tenant_id, top_k=max(top_k, 12))
        for h in hits:
            meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
            for v in (meta.get("ref"), meta.get("slug_hint"), meta.get("title"), meta.get("url")):
                if v:
                    hit_texts.append(str(v).strip().lower())
    except Exception as exc:
        logger.debug("build_layout_block: grounding search fail-open: %s", exc)

    unresolved: list[str] = []
    for ref in source_refs:
        r = str(ref).strip().lower()
        if not r:
            unresolved.append(ref)
            continue
        if r in known:
            continue
        if any(r == t or r in t or t in r for t in hit_texts):
            continue
        unresolved.append(ref)
    return unresolved


# composite leaf kinds whose content is free-form authored text -- source of
# the "proportional citation" requirement (see _validate_composite_grounding).
# Field per kind mirrors CatPortfolio's src/blocks/Composite.tsx Leaf switch.
_COMPOSITE_TEXT_LEAF_FIELDS: dict[str, tuple[str, ...]] = {
    "text": ("markdown", "text"),
    "quote": ("text",),
    "kv": ("value",),
    "stat": ("value",),
    "metric": ("value",),
    "badgeCloud": ("items",),
    "tagRow": ("items",),
    "card": ("title", "body", "markdown"),
}
_COMPOSITE_CITATION_CHAR_FLOOR = 400
# image/media/link leaves render on a public HR-facing page -- an
# agent-authored src/href becomes a live third-party request, so unlike
# other leaf props (free-form per plugins.portfolio_plugin.schema.ui_layout_schema's design) these
# three are protocol-checked.
_COMPOSITE_URL_LEAF_FIELDS: dict[str, tuple[str, ...]] = {
    "image": ("src",),
    "media": ("src",),
    "link": ("href",),
}
_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _composite_text_chars(children: Any) -> int:
    """Recursively sum authored text-bearing leaf content across the composite
    tree (containers hold ``children``; leaves don't)."""
    total = 0
    if not isinstance(children, list):
        return total
    for node in children:
        if not isinstance(node, dict):
            continue
        kind = str(node.get("kind") or "")
        if isinstance(node.get("children"), list):
            total += _composite_text_chars(node["children"])
            continue
        for field in _COMPOSITE_TEXT_LEAF_FIELDS.get(kind, ()):
            val = node.get(field)
            if isinstance(val, str):
                total += len(val)
            elif isinstance(val, list):
                total += sum(len(str(v)) for v in val if isinstance(v, str))
    return total


def _composite_media_allowlist() -> list[str]:
    """Optional ``settings.composite_media_allowlist`` (manifest) of allowed
    hostnames for image/media leaf ``src``. Empty (default) means "any http(s)
    host" -- the protocol check is the mandatory guard; the allowlist is an
    opt-in extra layer for deployments that want to pin sources."""
    try:
        from plugins.portfolio_plugin.plugin_config import SETTINGS

        raw = SETTINGS.get("composite_media_allowlist") if isinstance(SETTINGS, dict) else None
        if isinstance(raw, list):
            return [str(h).strip().lower() for h in raw if str(h).strip()]
    except Exception:
        logger.debug("block_builder.py: swallowed exception", exc_info=True)
    return []


def _composite_url_errors(children: Any, *, path: str = "children") -> list[str]:
    """Reject non-http(s) src/href/url on image/media/link leaves -- no
    relative paths, no data: URIs, on a page an unauthenticated visitor loads.
    image/media additionally check the optional host allowlist (link does
    not -- outbound links are expected to point off-site)."""
    from urllib.parse import urlparse

    errors: list[str] = []
    if not isinstance(children, list):
        return errors
    allowlist = _composite_media_allowlist()
    for i, node in enumerate(children):
        if not isinstance(node, dict):
            continue
        kind = str(node.get("kind") or "")
        node_path = f"{path}[{i}]"
        if isinstance(node.get("children"), list):
            errors.extend(_composite_url_errors(node["children"], path=f"{node_path}.children"))
            continue
        for field in _COMPOSITE_URL_LEAF_FIELDS.get(kind, ()):
            val = node.get(field)
            if not isinstance(val, str) or not val:
                continue
            if not _HTTP_URL_RE.match(val):
                errors.append(
                    f"composite.{node_path}.{field}: must be http(s):// (got {val[:60]!r})"
                )
                continue
            if kind in ("image", "media") and allowlist:
                host = (urlparse(val).hostname or "").lower()
                if host not in allowlist:
                    errors.append(
                        f"composite.{node_path}.{field}: host '{host}' not in composite_media_allowlist"
                    )
    return errors


def _validate_composite_grounding(props: dict, refs: list[str]) -> list[str]:
    """Composite-only grounding on top of the generic source_refs requirement:
    proportional citation (more authored text needs more refs -- one ref can't
    launder a whole authored page) + the image/media/link URL guard above."""
    errors = _composite_url_errors(props.get("children"))
    chars = _composite_text_chars(props.get("children"))
    if chars > _COMPOSITE_CITATION_CHAR_FLOOR:
        need = math.ceil(chars / _COMPOSITE_CITATION_CHAR_FLOOR)
        if len(refs) < need:
            errors.append(
                f"composite: {chars} chars of authored text needs >= {need} source_refs "
                f"(got {len(refs)}) -- proportional citation"
            )
    return errors


def _validate_and_stamp(block: dict, refs: list[str]) -> tuple[dict | None, list[str]]:
    """Validate a single block and embed ``_sourceRefs`` for design_layout's
    meta.sources aggregation (composer.compose_custom_layout pops the key
    before final validation — never reaches the persisted layout as a block
    prop). Returns (block_dict_or_None, errors)."""
    candidate = {
        "version": 1,
        "meta": {"audience": "default", "generatedAt": "1970-01-01T00:00:00Z"},
        "blocks": [block],
    }
    validated, verr = validate_layout(candidate)
    if verr:
        return None, verr
    out = validated["blocks"][0]
    if refs:
        out["_sourceRefs"] = list(refs)
    return out, []




def _build_proof_kpi(ctx: dict[str, Any], overrides: dict[str, Any]) -> list[dict]:
    """KPI grid from project metrics (ex-fragments helper)."""
    items = []
    projects = list(ctx.get("projects") or [])
    slugs = overrides.get("slugs") if isinstance(overrides.get("slugs"), list) else None
    if slugs:
        projects = [p for p in projects if p.get("slug") in slugs]
    for proj in projects:
        for m in proj.get("metrics") or []:
            if isinstance(m, dict) and m.get("label") and m.get("value"):
                items.append({
                    "label": str(m["label"]),
                    "value": str(m["value"]),
                    "delta": str(m["delta"]) if m.get("delta") else None,
                })
            if len(items) >= 6:
                break
        if len(items) >= 6:
            break
    if not items:
        return []
    return [{
        "type": "kpiGrid",
        "id": "kpi-proof",
        "props": {"items": [{k: v for k, v in it.items() if v is not None} for it in items]},
    }]


def _build_proof_timeline(ctx: dict[str, Any], overrides: dict[str, Any]) -> list[dict]:
    items = []
    projects = list(ctx.get("projects") or [])
    limit = overrides.get("limit") if isinstance(overrides.get("limit"), int) else 6
    from plugins.portfolio_plugin.compose.display_copy import author_display_copy
    from plugins.portfolio_plugin.discovery.period import format_period

    # Newest first — falls back to insertion order when no project has a
    # known date (sorts equal, Python sort is stable).
    projects = sorted(
        projects,
        key=lambda p: str(p.get("ended_on") or p.get("started_on") or "") if isinstance(p, dict) else "",
        reverse=True,
    )

    for proj in projects[:limit]:
        if not isinstance(proj, dict):
            continue
        body = author_display_copy(proj, form="fish_blurb")
        date_label = format_period(proj.get("started_on"), proj.get("ended_on")) or "shipped"
        items.append({
            "date": date_label,
            "title": proj.get("name") or proj.get("slug") or "project",
            "body": body,
            "tag": (proj.get("tags") or [None])[0] if proj.get("tags") else None,
        })
    if not items:
        return []
    return [{
        "type": "timeline",
        "id": "tl-proof",
        "props": {
            "title": "Recent work",
            "items": [{k: v for k, v in it.items() if v is not None} for it in items],
        },
    }]


def _build_cta_quick_actions(_ctx: dict[str, Any], _o: dict[str, Any]) -> list[dict]:
    from plugins.portfolio_plugin.plugin_config import SETTINGS
    actions = SETTINGS.get("quick_actions") or []
    if not isinstance(actions, list) or not actions:
        actions = [
            {"label": "Infra work", "prompt": "Show me your infra / SRE work"},
            {"label": "Deepest system", "prompt": "What is the deepest system you built?"},
            {"label": "Recent ship", "prompt": "What did you ship last month?"},
        ]
    normalized = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        label = a.get("label")
        prompt = a.get("prompt")
        if label and prompt:
            item = {"label": str(label), "prompt": str(prompt)}
            if a.get("icon"):
                item["icon"] = str(a["icon"])
            normalized.append(item)
    if not normalized:
        return []
    return [{
        "type": "quickActions",
        "id": "cta-quick",
        "props": {"prompt": "Ask about the work:", "actions": normalized},
    }]

async def build_layout_block_impl(
    block_type: str,
    *,
    tenant_id: int,
    query: str = "",
    slugs: list[str] | None = None,
    top_k: int = 6,
    props: dict | None = None,
    source_refs: list[str] | None = None,
    block_id: str = "",
    layout: dict | None = None,
    kind: str = "auto",
) -> dict[str, Any]:
    """Core implementation behind the ``build_layout_block`` MCP tool.

    ``kind`` lets a plan step choose *how* a type is realized (mirrors
    ``LayoutStep.kind`` in ``layout_plan.py``):
      - ``"auto"`` (default) — today's dispatch, byte-identical.
      - ``"db"`` — force the DB/context-derived path; error if ``block_type``
        has no DB-derived form.
      - ``"authored"`` — skip DB-derived dispatch entirely and go straight to
        the agent-authored path (props + ``_verify_source_refs``), even for a
        type that is normally DB-derived (e.g. an authored ``chart`` with a
        narrative series instead of auto-derived metrics). Grounding is
        unweakened: the authored path still requires ``source_refs``.
    """
    block_type = (block_type or "").strip()
    if block_type not in SUPPORTED_BLOCK_TYPES:
        return {
            "status": "error",
            "errors": [f"block_type: unsupported '{block_type}' (allowed: {sorted(SUPPORTED_BLOCK_TYPES)})"],
        }

    kind = str(kind or "auto").strip().lower()
    if kind not in ("auto", "db", "authored"):
        kind = "auto"
    _db_eligible = _DB_DERIVED_TYPES | {_ARCH_TYPE}
    if kind == "db" and block_type not in _db_eligible:
        return {
            "status": "error",
            "errors": [
                f"kind: 'db' not supported for block_type '{block_type}' "
                f"(allowed: {sorted(_db_eligible)})"
            ],
        }

    top_k = max(1, min(int(top_k or 6), 20))
    refs_in = [str(r) for r in (source_refs or []) if str(r).strip()]
    layout_hint = layout if isinstance(layout, dict) else None

    block: dict | None = None
    derived_refs: list[str] = []
    errors: list[str] = []

    if block_type == _ARCH_TYPE and kind != "authored":
        block, derived_refs, errors = await _build_arch_diagram(
            tenant_id=tenant_id, query=query, slugs=slugs, top_k=top_k,
            block_id=block_id, props=props,
        )
        if errors:
            return {"status": "error", "errors": errors}
        if block is not None:
            block = _apply_layout_hint(block, layout_hint)
            out, verr = _validate_and_stamp(block, derived_refs)
            if verr:
                return {"status": "error", "errors": verr}
            return {"status": "ok", "block": out, "source_refs": derived_refs}
        if kind == "db":
            return {
                "status": "error",
                "errors": [
                    "kind: 'db' requires archDiagram without a real props.source "
                    "(thin/query-driven) — omit props.source or use kind='authored'"
                ],
            }
        # block is None + no errors → real `source` supplied; fall through to
        # the agent-authored path below (still tagged block_type="archDiagram").

    if block_type in _DB_DERIVED_TYPES and kind != "authored":
        block, derived_refs, errors = await _build_db_derived(
            block_type, tenant_id=tenant_id, query=query, slugs=slugs, top_k=top_k,
            block_id=block_id, props=props,
        )
        if errors:
            return {"status": "error", "errors": errors}
        # ``card`` may return a list of tiles (one per project).
        if isinstance(block, list):
            validated: list[dict] = []
            for b in block:
                if not isinstance(b, dict):
                    continue
                b = _apply_layout_hint(b, layout_hint) or b
                out, verr = _validate_and_stamp(b, derived_refs)
                if verr:
                    return {"status": "error", "errors": verr}
                validated.append(out)
            if not validated:
                return {"status": "error", "errors": ["card: empty after validation"]}
            return {
                "status": "ok",
                "block": validated[0],
                "blocks": validated,
                "source_refs": derived_refs,
            }
        if not isinstance(block, dict):
            return {
                "status": "error",
                "errors": [f"{block_type}: unexpected block shape returned from builder"],
            }
        block = _apply_layout_hint(block, layout_hint)
        out, verr = _validate_and_stamp(block, derived_refs)
        if verr:
            return {"status": "error", "errors": verr}
        return {"status": "ok", "block": out, "source_refs": derived_refs}

    # Agent-authored: prose / chart / comparison / codeSnippet / composite / arch-with-source.
    if not props or not isinstance(props, dict):
        return {"status": "error", "errors": [f"props: required for authored block type '{block_type}'"]}
    if not refs_in:
        return {
            "status": "error",
            "errors": ["source_refs: required for agent-authored blocks — cite the indexed docs or project sources this content is grounded in"],
        }

    unresolved = await _verify_source_refs(
        tenant_id=tenant_id, query=query, source_refs=refs_in, top_k=top_k
    )
    if unresolved:
        return {
            "status": "error",
            "errors": [
                f"source_refs: could not ground {unresolved!r} against indexed context or declared project sources — "
                "call search_portfolio_context first and cite refs it actually returned"
            ],
        }

    if block_type == "composite":
        composite_errors = _validate_composite_grounding(props, refs_in)
        if composite_errors:
            return {"status": "error", "errors": composite_errors}

    # Reject a title-only shell before it ships: comparison always lands here
    # (always agent-authored); chart/timeline only land here when the caller
    # forced kind="authored" (their normal path is DB-derived and already
    # errors on thin data — see _build_db_derived). Mirrors the empty-shell
    # keys in context_enrich.py / layout_jury.py — see the bake-parity
    # Playwright finding (an empty title-only comparison table shipped).
    _empty_content_key = {"comparison": "rows", "chart": "series", "timeline": "items"}.get(block_type)
    if _empty_content_key is not None:
        _content_val = props.get(_empty_content_key)
        if not isinstance(_content_val, list) or not _content_val:
            return {
                "status": "error",
                "errors": [
                    f"props.{_empty_content_key}: required non-empty for authored "
                    f"'{block_type}' — do not ship a title-only shell; add real "
                    f"{_empty_content_key} or skip the block"
                ],
            }

    candidate_block = {"type": block_type, "id": block_id or f"{block_type}-1", "props": props}
    candidate_block = _apply_layout_hint(candidate_block, layout_hint) or candidate_block
    out, verr = _validate_and_stamp(candidate_block, refs_in)
    if verr:
        return {"status": "error", "errors": verr}
    return {"status": "ok", "block": out, "source_refs": refs_in}
