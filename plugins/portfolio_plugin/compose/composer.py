"""
Portfolio Layout Composer.

Deterministic composition of layout.json configurations.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from plugins.portfolio_plugin.plugin_config import SETTINGS
from plugins.portfolio_plugin.store import list_projects
from plugins.portfolio_plugin.compose.dag import (
    DAG_COLS_BY_LEVEL as _DAG_COLS_BY_LEVEL,
    DAG_LEVEL_BY_TYPE as _DAG_LEVEL_BY_TYPE,
    stamp_dag_from_blocks as _stamp_dag_from_blocks,
)
from plugins.portfolio_plugin.compose.ranking import (
    _content_depth,
    _infer_domain,
    _priority_bias,
    _project_family,
    _project_lexical_score,
    _query_tokens,
    rank_projects_by_query,
)
from plugins.portfolio_plugin.schema.ui_layout_schema import (
    BlockLayoutHint,
    UILayout,
    UILayoutMeta,
    HeroBlock,
    HeroProps,
    ProjectGridBlock,
    ProjectGridProps,
    UIProject,
    StatStripBlock,
    StatStripProps,
    StarStoryBlock,
    StarStoryProps,
    CardBlock,
    CardProps,
    UIStat,
    UILink,
    validate_layout,
)

# Named sections the agent may reference without hand-writing project/STAR data.
COMPOSED_SECTION_NAMES = frozenset({
    "hero", "statStrip", "projectGrid", "starStory",
    "kpiGrid", "cards",  # matrix default (Open Design)
})

# Required prop keys for composed section types. Incomplete dict blocks with
# these types are rehydrated from DB builders instead of failing validation.
_COMPOSED_REQUIRED_PROPS: dict[str, frozenset[str]] = {
    "hero": frozenset({"name", "tagline"}),
    "statStrip": frozenset({"stats"}),
    "projectGrid": frozenset({"projects"}),
    "starStory": frozenset({"situation", "task", "action", "result"}),
    "kpiGrid": frozenset({"items"}),
    "cards": frozenset(),  # expands to N card blocks; never incomplete as a dict type
}

# Required props for archDiagram; thin agent stubs (e.g. only `query`) get filled.
_ARCH_REQUIRED_PROPS = frozenset({"title", "kind", "source"})

logger = logging.getLogger("whiskers.plugins.portfolio.composer")

# Matrix default (Open Design): hero → impact KPIs → domain cards → STAR proof.
# ``cards`` expands to one ``card`` block per project (domain-tinted).
AUDIENCE_TEMPLATES = {
    "default": {
        "sections": ["hero", "kpiGrid", "cards", "starStory"],
        "star_query": "impact architecture results",
        "star_k": 2,
        "max_projects": 6,
    },
    "recruiter": {
        "sections": ["hero", "kpiGrid", "cards", "starStory"],
        "star_query": "measurable impact results delivery",
        "star_k": 2,
        "max_projects": 4,
    },
    "hiring-manager": {
        "sections": ["hero", "kpiGrid", "starStory", "cards"],
        "star_query": "ownership tradeoffs leadership delivery",
        "star_k": 3,
        "max_projects": 6,
    },
    "peer": {
        "sections": ["hero", "cards", "starStory", "kpiGrid"],
        "star_query": "architecture systems design deep dive",
        "star_k": 3,
        "max_projects": 8,
    },
}


def get_audience_template(audience: str) -> tuple[str, dict]:
    """Resolve the effective audience template (base merged with manifest settings).

    Returns the normalized audience and the merged template dict.
    """
    if audience not in AUDIENCE_TEMPLATES:
        audience = "default"
    template = AUDIENCE_TEMPLATES[audience].copy()
    settings_audience = SETTINGS.get("audiences", {}).get(audience)
    if isinstance(settings_audience, dict):
        template.update(settings_audience)
    return audience, template


def _tokenize(text: str) -> set[str]:
    """Lowercase word-tokenize free text into a set for keyword-overlap scoring."""
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _score_audience(tokens: set[str]) -> str:
    """Deterministic keyword-overlap scoring against each audience template's star_query.

    Argmax over token overlap; ties (including all-zero) fall back to "default"
    since it's checked first in AUDIENCE_TEMPLATES' iteration order.
    """
    best_audience = "default"
    best_score = -1
    for audience, template in AUDIENCE_TEMPLATES.items():
        query_tokens = _tokenize(template.get("star_query", ""))
        score = len(tokens & query_tokens)
        if score > best_score:
            best_score = score
            best_audience = audience
    return best_audience


def infer_audience_from_job_signals(
    job_description: str, extracted_skills: list[str] | None = None
) -> tuple[str, str | None]:
    """Infer the closest audience template + star_query from job posting signals.

    Non-LLM, deterministic: tokenizes the job description (+ optional extracted
    skills) and scores overlap against each AUDIENCE_TEMPLATES star_query.
    """
    text = job_description or ""
    if extracted_skills:
        text = f"{text} {' '.join(extracted_skills)}"
    tokens = _tokenize(text)
    audience = _score_audience(tokens)
    _, template = get_audience_template(audience)
    return audience, template.get("star_query")


def _hero(settings: dict | None) -> HeroBlock:
    """Build HeroBlock from hero settings."""
    if not settings:
        settings = {}
    name = settings.get("name") or "Andrew (the cat)"
    tagline = settings.get("tagline") or ""
    pitch = settings.get("pitch")

    links_list = settings.get("links")
    valid_links = []
    if isinstance(links_list, list):
        for link in links_list:
            if not isinstance(link, dict):
                continue
            href = link.get("href", "")
            label = link.get("label", "")
            # Guard: both must be strings and href must be http(s) to avoid AttributeError
            if isinstance(href, str) and isinstance(label, str) and (
                href.startswith("http://") or href.startswith("https://")
            ):
                valid_links.append(UILink(label=label, href=href))
            else:
                logger.warning(f"Dropping invalid hero link: {link}")

    props = HeroProps(
        name=name,
        tagline=tagline,
        pitch=pitch,
        links=valid_links if valid_links else None,
    )
    return HeroBlock(id="hero-1", props=props)


def _stat_strip(projects: list[dict]) -> StatStripBlock | None:
    """Collect truthy headline metrics across projects, dedupe by label, cap at 4."""
    stats = []
    seen_labels = set()
    for proj in projects:
        metrics = proj.get("metrics")
        if not metrics or not isinstance(metrics, list):
            continue
        for m in metrics:
            if not isinstance(m, dict):
                continue
            if m.get("headline"):
                label = m.get("label")
                value = m.get("value")
                if label and value:
                    if label not in seen_labels:
                        seen_labels.add(label)
                        stats.append(UIStat(label=label, value=value))
                        if len(stats) == 4:
                            break
        if len(stats) == 4:
            break

    if not stats:
        return None

    return StatStripBlock(id="stats-1", props=StatStripProps(stats=stats))


def _project_metrics_and_links(proj: dict) -> tuple[list[UIStat], list[UILink]]:
    """Back-compat alias — canonical home is compose.quality.project_metrics_and_links."""
    from plugins.portfolio_plugin.compose.quality import project_metrics_and_links

    return project_metrics_and_links(proj)


def _project_grid(projects: list[dict]) -> ProjectGridBlock | None:
    """Build ProjectGridBlock from a list of project dicts, filtering invalid links."""
    if not projects:
        return None

    ui_projects = []
    for proj in projects:
        slug = proj.get("slug")
        name = proj.get("name") or ""
        summary = proj.get("summary") or ""
        tags = proj.get("tags") or []
        metrics, links = _project_metrics_and_links(proj)

        ui_projects.append(
            UIProject(
                id=slug or "",
                name=name,
                summary=summary,
                tags=tags,
                metrics=metrics,
                links=links,
            )
        )

    return ProjectGridBlock(id="projects-1", props=ProjectGridProps(projects=ui_projects))


def _project_cards(projects: list[dict]) -> list[CardBlock]:
    """One domain-tinted card per project (matrix L2 Projects band).

    Card body is **authored** from inventory context (name/tags/metrics +
    summary as seed) — never a raw ``portfolio_projects.summary`` paste.
    """
    if not projects:
        return []
    from plugins.portfolio_plugin.compose.display_copy import author_display_copy
    from plugins.portfolio_plugin.compose.quality import filter_projects_for_layout

    projects = filter_projects_for_layout(projects)
    out: list[CardBlock] = []
    seen_slugs: set[str] = set()
    for i, proj in enumerate(projects):
        if not isinstance(proj, dict):
            continue
        slug = str(proj.get("slug") or f"proj-{i}")
        slug_key = slug.lower()
        if slug_key in seen_slugs:
            continue
        seen_slugs.add(slug_key)
        name = str(proj.get("name") or slug)
        body = author_display_copy(proj, form="card_body")
        tags = list(proj.get("tags") or []) if isinstance(proj.get("tags"), list) else []
        metrics, links = _project_metrics_and_links(proj)
        domain = _infer_domain(proj)
        # Stable id for meta.dag node refs: card-<slug>
        safe = re.sub(r"[^a-z0-9_-]+", "-", slug.lower()).strip("-") or f"p{i}"
        # Default half-width so L2 sits as a 2-col matrix (pairs well with
        # meta.dag cols:2). Agents may override via LayoutStep.layout.span.
        out.append(
            CardBlock(
                id=f"card-{safe}",
                props=CardProps(
                    title=name,
                    eyebrow=(tags[0] if tags else None),
                    body=body,
                    tags=[str(t) for t in tags if t] or None,
                    metrics=metrics or None,
                    links=links or None,
                    domain=domain,  # type: ignore[arg-type]
                ),
                layout=BlockLayoutHint(span=6),
            )
        )
    return out


def _kpi_grid(projects: list[dict]) -> dict | None:
    """KPI tiles from project metrics (matrix L1 Impact)."""
    items: list[dict] = []
    for proj in projects:
        if not isinstance(proj, dict):
            continue
        for m in proj.get("metrics") or []:
            if isinstance(m, dict):
                label = m.get("label")
                value = m.get("value")
                # is-not-None + non-blank check (not truthiness) so a
                # legitimate zero-valued metric ("0 downtime") survives.
                if label is not None and value is not None and str(label).strip() and str(value).strip():
                    item: dict[str, Any] = {
                        "label": str(label),
                        "value": str(value),
                    }
                    if m.get("delta"):
                        item["delta"] = str(m["delta"])
                    items.append(item)
            if len(items) >= 4:
                break
        if len(items) >= 4:
            break
    if not items:
        # Fall back to headline-style stats via stat_strip shape
        ss = _stat_strip(projects)
        if ss is None:
            return None
        for s in ss.props.stats:
            items.append({"label": s.label, "value": s.value})
            if len(items) >= 4:
                break
    if not items:
        return None
    return {
        "type": "kpiGrid",
        "id": "kpi-master",
        "props": {"items": items},
    }


def _star_stories(stories: list[dict]) -> list[StarStoryBlock]:
    """Map content vector rows to StarStoryBlocks, skipping malformed ones."""
    blocks = []
    idx = 1
    for row in stories:
        meta = row.get("metadata")
        if not meta or not isinstance(meta, dict):
            continue
        situation = meta.get("situation")
        task = meta.get("task")
        action = meta.get("action")
        result = meta.get("result")

        if not (situation and task and action and result):
            continue

        tags = meta.get("tags") or []

        props = StarStoryProps(
            situation=situation,
            task=task,
            action=action,
            result=result,
            tags=tags,
        )
        blocks.append(StarStoryBlock(id=f"star-{idx}", props=props))
        idx += 1
    return blocks


def _mermaid_escape(text: str) -> str:
    """Sanitize a label for mermaid node text (double-quoted)."""
    return (text or "").replace("\\", "/").replace('"', "'").replace("\n", " ").strip()


def _default_mermaid_source(projects: list[dict], settings: dict | None = None) -> str:
    """Build a truthful mermaid graph from hero + project names (no invented metrics)."""
    hero = (settings or {}).get("hero") or {}
    root = _mermaid_escape(hero.get("name") or "Portfolio")
    lines = ["graph TD", f'  ROOT["{root}"]']
    if projects:
        for i, proj in enumerate(projects[:6]):
            if not isinstance(proj, dict):
                continue
            label = _mermaid_escape(proj.get("name") or proj.get("slug") or f"project-{i}")
            if not label:
                continue
            pid = f"P{i}"
            lines.append(f'  {pid}["{label}"]')
            lines.append(f"  ROOT --> {pid}")
            tags = proj.get("tags") or []
            if isinstance(tags, list) and tags:
                tlabel = _mermaid_escape(str(tags[0]))
                if tlabel:
                    lines.append(f'  {pid}T["{tlabel}"]')
                    lines.append(f"  {pid} --> {pid}T")
    else:
        # Generic OCT-shaped skeleton when the tenant has no projects yet.
        lines.extend(
            [
                '  MCP["MCP Server"]',
                '  GOAP["GOAP Agent"]',
                '  PLUG["Portfolio Plugin"]',
                "  ROOT --> MCP",
                "  MCP --> GOAP",
                "  GOAP --> PLUG",
            ]
        )
    return "\n".join(lines)


def _is_incomplete_composed_block(sec: dict) -> bool:
    """True when a dict section is a composed type missing required props."""
    if not isinstance(sec, dict):
        return False
    block_type = sec.get("type")
    if block_type not in COMPOSED_SECTION_NAMES:
        return False
    props = sec.get("props")
    if not isinstance(props, dict) or not props:
        return True
    required = _COMPOSED_REQUIRED_PROPS.get(block_type, frozenset())
    for key in required:
        val = props.get(key)
        if val is None or val == "" or val == []:
            return True
    return False


def _is_incomplete_arch_diagram(sec: dict) -> bool:
    """True when an archDiagram dict is missing title/kind/source."""
    if not isinstance(sec, dict) or sec.get("type") != "archDiagram":
        return False
    props = sec.get("props")
    if not isinstance(props, dict):
        return True
    for key in _ARCH_REQUIRED_PROPS:
        val = props.get(key)
        if val is None or val == "":
            return True
    return False


def _compose_named_section(
    name: str,
    *,
    projects: list,
    stories: list,
    block_id: str | None = None,
) -> list:
    """Build DB-backed block(s) for a composed section name.

    Returns a list (starStory / cards may yield multiple; missing optional → []).
    """
    built: list = []
    if name == "hero":
        block = _hero(SETTINGS.get("hero"))
        # Stable id for matrix dag L0 (matches CatPortfolio baked layout).
        hid = block_id or "h1"
        block = block.model_copy(update={"id": hid})
        built.append(block)
    elif name == "statStrip":
        ss = _stat_strip(projects)
        if ss is not None:
            if block_id:
                ss = ss.model_copy(update={"id": block_id})
            built.append(ss)
    elif name == "kpiGrid":
        kg = _kpi_grid(projects)
        if kg is not None:
            if block_id:
                kg = {**kg, "id": block_id}
            built.append(kg)
    elif name == "projectGrid":
        pg = _project_grid(projects)
        if pg is not None:
            if block_id:
                pg = pg.model_copy(update={"id": block_id})
            built.append(pg)
    elif name == "cards":
        cards = _project_cards(projects)
        if block_id and cards:
            # First card can take an explicit id; rest keep card-<slug>.
            cards[0] = cards[0].model_copy(update={"id": block_id})
        built.extend(cards)
    elif name == "starStory":
        for idx, story in enumerate(_star_stories(stories)):
            if block_id and idx == 0:
                story = story.model_copy(update={"id": block_id})
            built.append(story)
    return built


def _svg_data_from_projects(motif: str, projects: list[dict]) -> dict:
    """Ground svg_render motif input in real project data (no invented content)."""
    if motif == "timeline":
        return {"items": [{"title": p.get("name") or p.get("slug") or ""} for p in (projects or [])[:6] if isinstance(p, dict)]}
    if motif == "stack":
        return {
            "layers": [
                {"label": p.get("name") or p.get("slug") or "", "sublabel": str(p.get("summary") or "")[:60]}
                for p in (projects or [])[:5]
                if isinstance(p, dict)
            ]
        }
    if motif == "metric_ring":
        metrics: list[dict] = []
        for p in projects or []:
            if not isinstance(p, dict):
                continue
            for m in p.get("metrics") or []:
                if isinstance(m, dict) and m.get("headline") and m.get("label") and m.get("value") is not None:
                    metrics.append({"label": m["label"], "value": m["value"]})
                if len(metrics) >= 4:
                    break
            if len(metrics) >= 4:
                break
        return {"metrics": metrics}
    if motif == "topology":
        hero = SETTINGS.get("hero") or {}
        root_id = "root"
        nodes = [{"id": root_id, "label": hero.get("name") or "Portfolio"}]
        edges = []
        for i, p in enumerate((projects or [])[:6]):
            if not isinstance(p, dict):
                continue
            pid = f"p{i}"
            nodes.append({"id": pid, "label": p.get("name") or p.get("slug") or f"project-{i}"})
            edges.append({"from": root_id, "to": pid})
        return {"nodes": nodes, "edges": edges}
    return {}


def _enrich_arch_diagram(sec: dict, projects: list[dict], *, theme: str = "") -> dict:
    """Fill missing archDiagram props from query/title hints + portfolio mermaid
    (kind="mermaid", default) or a generated SVG motif (kind="svg" + props.motif,
    see svg_render.py -- Phase 6a)."""
    props_in = sec.get("props") if isinstance(sec.get("props"), dict) else {}
    props: dict = dict(props_in)

    title = props.get("title")
    if not title:
        # Agents often pass a free-text `query` instead of schema fields.
        title = props.get("query") or props.get("caption") or props.get("label")
    if not title:
        hero = SETTINGS.get("hero") or {}
        title = hero.get("name") or "System architecture"
    title = str(title).strip() or "System architecture"

    kind = props.get("kind")
    if kind not in ("mermaid", "svg"):
        kind = "mermaid"

    source = props.get("source")
    motif = str(props.get("motif") or "").strip()
    if source and str(source).strip():
        source = str(source)
    elif kind == "svg" and motif:
        from plugins.portfolio_plugin.render.svg_render import render_motif

        data = _svg_data_from_projects(motif, projects)
        svg = render_motif(motif, data, title=title, theme=theme) if theme else render_motif(motif, data, title=title)
        if svg:
            source = svg
        else:
            # Unknown motif / oversized output -- fall through to mermaid
            # rather than emit a mermaid string tagged kind="svg" (the FE
            # would render it as literal mermaid text inside an <img>).
            kind = "mermaid"
            source = _default_mermaid_source(projects, SETTINGS)
    elif kind == "svg":
        # kind="svg" with no source and no motif: nothing to render as SVG --
        # downgrade to the mermaid fallback instead of tagging mermaid
        # syntax as kind="svg" (previously mismatched; see _default_mermaid_source).
        kind = "mermaid"
        source = _default_mermaid_source(projects, SETTINGS)
    else:
        source = _default_mermaid_source(projects, SETTINGS)

    block_id = sec.get("id") if isinstance(sec.get("id"), str) and sec.get("id") else "arch-1"
    return {
        "type": "archDiagram",
        "id": block_id,
        "props": {
            "title": title,
            "kind": kind,
            "source": source,
        },
    }


def _merge_live_source_into_project(project: dict[str, Any], sources: list[dict]) -> dict[str, Any]:
    """Merge successful live fetch results into a shallow project copy (no DB write)."""
    out = dict(project)
    tags = list(out.get("tags") or []) if isinstance(out.get("tags"), list) else []
    metrics = list(out.get("metrics") or []) if isinstance(out.get("metrics"), list) else []
    tag_set = {str(t).lower() for t in tags if t is not None}
    metric_labels = {
        str(m.get("label")).lower()
        for m in metrics
        if isinstance(m, dict) and m.get("label") is not None
    }

    for src in sources:
        if not isinstance(src, dict) or src.get("status") != "ok":
            continue
        kind = (src.get("kind") or "").lower()
        source_use = src.get("source_use") or []
        if not isinstance(source_use, list):
            source_use = []

        meta = src.get("meta") if isinstance(src.get("meta"), dict) else {}
        content = src.get("content")

        # summary: only when source declared use includes "summary" (or github meta desc)
        if "summary" in source_use and isinstance(content, str) and content.strip():
            # Prefer first non-empty line / short blurb over full readme
            first = content.strip().split("\n", 1)[0].strip()
            if first and not first.startswith("#"):
                out["summary"] = first[:500]
            elif meta.get("description"):
                out["summary"] = str(meta["description"])[:500]
        elif kind == "github" and meta.get("description") and not (out.get("summary") or "").strip():
            out["summary"] = str(meta["description"])[:500]

        topics = meta.get("topics") if isinstance(meta.get("topics"), list) else []
        for t in topics:
            if t is None:
                continue
            t_str = str(t).strip()
            if t_str and t_str.lower() not in tag_set:
                tag_set.add(t_str.lower())
                tags.append(t_str)

        if kind == "github":
            stars = meta.get("stars")
            if stars is not None and "stars" not in metric_labels:
                metrics.append({"label": "stars", "value": str(stars)})
                metric_labels.add("stars")
            pushed = meta.get("pushed_at")
            if pushed and "pushed" not in metric_labels:
                # compact date if ISO
                val = str(pushed)[:10] if isinstance(pushed, str) else str(pushed)
                metrics.append({"label": "pushed", "value": val})
                metric_labels.add("pushed")

    out["tags"] = tags
    out["metrics"] = metrics
    return out


def _hits_to_source_results(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map indexed context hits into the shape ``_merge_live_source_into_project`` expects."""
    sources: list[dict[str, Any]] = []
    for h in hits:
        meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
        kind = str(meta.get("kind") or "github")
        text = h.get("content_text") or ""
        # First non-heading line as summary-like content
        summary_line = ""
        for line in str(text).splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                summary_line = line[:500]
                break
        meta_out: dict[str, Any] = {
            "description": summary_line or meta.get("title"),
            "topics": meta.get("tags") if isinstance(meta.get("tags"), list) else [],
            "full_name": meta.get("ref") or meta.get("title"),
        }
        sources.append(
            {
                "status": "ok",
                "kind": kind,
                "ref": meta.get("ref"),
                "meta": meta_out,
                "content": text[:4000] if text else summary_line,
                "source_use": ["summary", "meta"],
            }
        )
    return sources


async def _live_enrich(
    projects: list[dict],
    tenant_id: int = 1,
    budget_s: float = 8.0,
) -> list[dict]:
    """Fail-open refresh of project fields — index-first, live network past TTL.

    1. Query ``portfolio_plugin__context`` per slug.
    2. When hits are fresher than ``discovery.freshness_s``, merge index docs.
    3. Otherwise fall through to live ``get_project_context`` (existing path).
    Never writes the DB; timeouts/errors leave the original row.
    """
    if not projects:
        return projects

    # Only enrich rows that declare sources (live path) — index can still help.
    targets = [
        p for p in projects
        if isinstance(p, dict) and (p.get("context_sources") or [])
    ]
    if not targets:
        return projects

    freshness_s = 21600
    try:
        disc = SETTINGS.get("discovery") if isinstance(SETTINGS, dict) else {}
        if isinstance(disc, dict) and disc.get("freshness_s") is not None:
            freshness_s = int(disc["freshness_s"])
    except (TypeError, ValueError) as exc:
        logger.debug("live enrich freshness_s defaulted: %s", exc)

    try:
        from plugins.portfolio_plugin.discovery.index import docs_for_slug, is_fresh
        from plugins.portfolio_plugin.MCPTools.context_tools import get_project_context
    except Exception as exc:
        logger.warning("live enrich unavailable (import): %s", exc)
        return projects

    sem = asyncio.Semaphore(4)
    slug_to_merged: dict[str, dict] = {}

    async def _one(proj: dict) -> None:
        slug = proj.get("slug")
        if not slug:
            return
        async with sem:
            # Index-first path
            extra_refs = [
                str(src.get("ref") or "")
                for src in (proj.get("context_sources") or [])
                if isinstance(src, dict) and src.get("ref")
            ]
            try:
                hits = await docs_for_slug(str(slug), tenant_id=tenant_id, top_k=6, extra_refs=extra_refs)
                if hits and is_fresh(hits, freshness_s):
                    sources = _hits_to_source_results(hits)
                    slug_to_merged[str(slug)] = _merge_live_source_into_project(proj, sources)
                    return
                if hits:
                    # Stale but usable — merge, then optionally refresh live
                    sources = _hits_to_source_results(hits)
                    proj = _merge_live_source_into_project(proj, sources)
            except Exception as exc:
                logger.debug("index enrich %s failed: %s", slug, exc)

            # Live network fallthrough
            try:
                from core.context import current_tenant_id

                token = current_tenant_id.set(tenant_id)
                try:
                    res = await get_project_context(slug=str(slug))
                finally:
                    current_tenant_id.reset(token)
            except Exception as exc:
                logger.debug("live enrich %s failed: %s", slug, exc)
                if str(slug) not in slug_to_merged:
                    # keep partial index merge if we had one
                    if proj is not None and proj.get("slug"):
                        slug_to_merged[str(slug)] = proj
                return
            if not isinstance(res, dict) or res.get("status") != "ok":
                if str(slug) not in slug_to_merged:
                    slug_to_merged[str(slug)] = proj
                return
            sources = res.get("sources") or []
            if not isinstance(sources, list):
                return
            slug_to_merged[str(slug)] = _merge_live_source_into_project(proj, sources)

    async def _run_all() -> None:
        await asyncio.gather(*[_one(p) for p in targets], return_exceptions=True)

    try:
        await asyncio.wait_for(_run_all(), timeout=float(budget_s))
    except asyncio.TimeoutError:
        logger.info("live enrich budget %.1fs exceeded; using partial merges", budget_s)
    except Exception as exc:
        logger.warning("live enrich failed open: %s", exc)
        return projects

    if not slug_to_merged:
        return projects

    out: list[dict] = []
    for p in projects:
        if not isinstance(p, dict):
            out.append(p)
            continue
        slug = p.get("slug")
        out.append(slug_to_merged.get(str(slug), p) if slug is not None else p)
    return out


async def compose_layout(
    audience: str = "default",
    star_query: str | None = None,
    star_k: int | None = None,
    tenant_id: int = 1,
    refresh: bool = False,
    *,
    _skip_scoped: bool = False,
) -> dict:
    """Compose layout based on audience template and project/story data.

    When ``refresh=True``, projects with ``context_sources`` are fail-open
    live-enriched (no DB write) before builders run. Project order prefers
    semantic hits on the portfolio context index when available.

    ``_skip_scoped`` is used by ``compose_scoped_layout`` template fallback to
    avoid recursive scoped → template → scoped loops.
    """
    if not _skip_scoped:
        try:
            from plugins.portfolio_plugin.compose.compose_scoped import compose_scoped_layout
            q = star_query or audience or "agent infra architecture systems design"
            res = await compose_scoped_layout(
                query=q, tenant_id=tenant_id, refresh=refresh
            )
            if isinstance(res, dict) and res.get("status") == "ok" and res.get("layout"):
                # Prefer scoped GenUI when it produces a real multi-block layout.
                layout = res["layout"]
                if isinstance(layout, dict) and len(layout.get("blocks") or []) >= 2:
                    return layout
        except Exception as exc:
            logger.warning("compose_layout scoped fail-open: %s", exc)

    audience, template = get_audience_template(audience)

    proj_audience = None if audience == "default" else audience
    projects = await list_projects(audience=proj_audience, tenant_id=tenant_id)
    max_projects = template.get("max_projects", 6)
    rank_q = star_query or template.get("star_query") or audience
    projects = await rank_projects_by_query(
        projects, rank_q, tenant_id=tenant_id, top_k=max(12, int(max_projects) * 2)
    )
    projects = projects[:max_projects]
    if refresh:
        projects = await _live_enrich(projects, tenant_id=tenant_id)

    # star_query is a rank/context query only — portfolio_star collection retired.
    # Template starStory sections omit content unless authored elsewhere.
    stories: list = []
    _ = star_k  # retained on wire for back-compat; unused

    blocks: list = []
    sections = template.get("sections", [])
    for sec in sections:
        if not isinstance(sec, str):
            continue
        blocks.extend(
            _compose_named_section(sec, projects=projects, stories=stories)
        )

    # Dump to plain dicts so dag stamping + validation are uniform.
    raw_blocks: list[dict] = []
    for b in blocks:
        if hasattr(b, "model_dump"):
            raw_blocks.append(b.model_dump(mode="json", exclude_none=True))
        elif isinstance(b, dict):
            raw_blocks.append(b)

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    meta: dict[str, Any] = {
        "audience": audience,
        "generatedAt": generated_at,
        # Match CatPortfolio baked matrix home (design/layout.yaml theme: cozy).
        "theme": "cozy",
    }
    dag = _stamp_dag_from_blocks(raw_blocks)
    if dag:
        meta["dag"] = dag

    candidate = {"version": 1, "meta": meta, "blocks": raw_blocks}
    layout, errors = validate_layout(candidate)
    if layout is not None:
        return layout
    logger.warning("compose_layout validate failed: %s", errors)
    # Last resort: hero-only without dag so callers still get something.
    return {
        "version": 1,
        "meta": {"audience": audience, "generatedAt": generated_at},
        "blocks": [
            _hero(SETTINGS.get("hero")).model_dump(mode="json", exclude_none=True)
        ],
    }


# Custom layout assembly lives in custom_layout.py; re-export for stable imports.
from plugins.portfolio_plugin.compose.custom_layout import (  # noqa: E402
    _merge_design_spec,
    compose_custom_layout,
)
