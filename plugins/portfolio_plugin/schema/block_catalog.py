"""Block feature catalog — per-type context for the layout agent's tool picking.

Single source of truth for "what is this block type, when do I reach for it, what
does it cost me". ``band``/``grounding`` are **derived**, not re-typed, from the
existing dispatch maps (``composer._DAG_LEVEL_BY_TYPE``,
``block_builder._DB_DERIVED_TYPES`` / ``_AUTHORED_TYPES``) so this catalog cannot
silently drift from what materialization actually does — only the prose fields
(``renders`` / ``when_to_use`` / ``avoid_when`` / ``props_signature``) are hand-authored.

Consumed by ``layout.evidence_pack.format_block_catalog`` (planner prompt) and
``get_design_context`` / public ``GET /portfolio/design-context`` (``block_catalog``
field) — both keep this as the one place to update when a block type's story changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger("whiskers.plugins.portfolio.block_catalog")

Grounding = Literal["db", "authored", "widget", "dual"]
SourceRefPolicy = Literal["required", "not_required", "required_if_authored"]


@dataclass(frozen=True)
class BlockFeature:
    """One block type's picking context for the layout agent."""

    type: str
    band_level: int
    band_label: str
    grounding: Grounding
    source_refs: SourceRefPolicy
    default_span: int
    props_signature: str
    renders: str
    when_to_use: str
    avoid_when: str = ""


# Hand-authored prose only — band/grounding/source_refs are filled in by
# ``_build_catalog()`` below from the existing dispatch maps, never here.
_PROSE: dict[str, dict[str, str]] = {
    "hero": {
        "props_signature": "{name, tagline, subtitle?, links?}",
        "renders": "Opening identity block — name, tagline, quick links.",
        "when_to_use": "Always one per page, first block.",
        "avoid_when": "Never omit; never use more than one.",
    },
    "kpiGrid": {
        "props_signature": "{items: [{label, value, delta?, spark?}]}",
        "renders": "Headline metric tiles with optional delta/sparkline.",
        "when_to_use": "Project metrics exist (counts, cost deltas, tool counts).",
        "avoid_when": "No real metrics on any scoped project — prefer statStrip or skip.",
    },
    "statStrip": {
        "props_signature": "{stats: [{label, value}]}",
        "renders": "Compact single-line metric strip, lighter than kpiGrid.",
        "when_to_use": "A few metrics exist but a full kpiGrid feels heavy.",
        "avoid_when": "kpiGrid already covers the same metrics.",
    },
    "card": {
        "props_signature": "{title, eyebrow?, body?, media?, metrics?, tags?, links?, badges?, tech?, domain?, accent?, variant?}",
        "renders": "Domain-tinted project tile — the primary Projects-band unit.",
        "when_to_use": "One step with top_k covering ranked projects; server expands to N tiles. Prefer explicit slugs.",
        "avoid_when": "Use projectGrid instead only for a flat multi-project dump with no per-card styling.",
    },
    "projectGrid": {
        "props_signature": "{projects: [...]}",
        "renders": "Flat multi-project grid, less tinted/styled than card.",
        "when_to_use": "Many projects need a compact scan list, not tiles.",
        "avoid_when": "card already covers the Projects band — don't ship both for the same set.",
    },
    "flowAnim": {
        "props_signature": "{title?, nodes: [{id,label,group?}], edges: [{from,to,label?}], animate?}",
        "renders": "Animated system/agent flow diagram — moving node graph.",
        "when_to_use": "Architecture band; brief mentions orchestration, routing, agent systems, pipelines.",
        "avoid_when": "archDiagram already tells the same architecture story statically.",
    },
    "archDiagram": {
        "props_signature": "DB-derived: {title?, query?} (auto mermaid). Authored: {title, kind:'mermaid'|'svg', source} + source_refs.",
        "renders": "Static architecture tree/diagram — mermaid or generated SVG motif.",
        "when_to_use": "Architecture band; real system structure exists to diagram.",
        "avoid_when": "Prefer flowAnim if the story is about live agent motion, not static structure.",
    },
    "chart": {
        "props_signature": "DB-derived: {} (metrics→bars). Authored: {kind, title?, series: [...], caption?, unit?} + source_refs.",
        "renders": "Quantitative bar/line/area/donut/radar chart.",
        "when_to_use": "Real numeric metrics exist (cost, latency, throughput, adoption) to compare.",
        "avoid_when": "No numeric evidence — never invent series values.",
    },
    "timeline": {
        "props_signature": "{title?, items: [{date,title,body?,tag?}]}",
        "renders": "Chronological arc of shipped work / phases.",
        "when_to_use": "Proof band; multiple projects/phases with a real sequence.",
        "avoid_when": "Fewer than 2 real items available.",
    },
    "starStory": {
        "props_signature": (
            "Authored from evidence pack (portfolio_projects + portfolio_plugin__context "
            "as context only): {situation, task, action, result, tags?} + source_refs. "
            "portfolio_star is retired."
        ),
        "renders": "One grounded STAR story (situation/task/action/result).",
        "when_to_use": "Proof band; evidence pack supports a real impact story for the role.",
        "avoid_when": "No grounded S/T/A/R in context — omit rather than invent.",
    },
    "prose": {
        "props_signature": "{markdown} + source_refs (required)",
        "renders": "Authored narrative markdown, deep-dive band.",
        "when_to_use": "One strong project has a summary worth expanding into a real deep dive.",
        "avoid_when": "No verified source_refs to ground the claims — server falls back to DB text without them.",
    },
    "composite": {
        "props_signature": "{children: [...]} (depth<=3, <=40 nodes) + source_refs (required)",
        "renders": "Nested GenUI cluster — split deep-dive layout of sub-blocks.",
        "when_to_use": "A deep dive needs more structure than plain prose (e.g. side-by-side code + narrative).",
        "avoid_when": "Keep depth <=3; do not nest for its own sake.",
    },
    "codeSnippet": {
        "props_signature": "{lang, code, caption?} + source_refs (required)",
        "renders": "Short syntax-highlighted code sample.",
        "when_to_use": "Evidence includes a real code excerpt worth showing verbatim.",
        "avoid_when": "No indexed source backs the snippet — never invent code.",
    },
    "comparison": {
        "props_signature": "{title?, columns: [{label}], rows: [{label, cells:[...]}]}",
        "renders": "Side-by-side tradeoff/option table.",
        "when_to_use": "2+ real rows of contrast exist (stacks, streams, before/after).",
        "avoid_when": "Fewer than 2 real rows — an empty/title-only table is rejected, don't ship a stub.",
    },
    "quickActions": {
        "props_signature": "{prompt?, actions: [{label, prompt, icon?}]}",
        "renders": "CTA / contact chips that seed visitor chat.",
        "when_to_use": "Always last block (Ask band).",
        "avoid_when": "Never omit on a full page; never use mid-page.",
    },
    "mcpSandbox": {
        "props_signature": "{} — client-owned defaults, no server props needed.",
        "renders": "Interactive mock MCP tool-call sandbox (no live backend, no PHI).",
        "when_to_use": "Architecture band; brief touches MCP/agent tooling — zero grounding cost to include.",
        "avoid_when": "Brief has nothing to do with MCP/agent tooling — reads as filler.",
    },
    "fishTank": {
        "props_signature": (
            "{renderer:'webgl', fish:[{slug,title,species,size,depth,speed,glow,school,...}], "
            "highlightSlugs?, tankTheme?, title?}"
        ),
        "renders": "WebGL aquarium — each project is a fish (size/depth/glow bounded 0..1).",
        "when_to_use": (
            "Optional Architecture band delight; only when fish_tank_enabled and FE supports it. "
            "Prefer server build_fish_tank_block over hand-authoring numerics."
        ),
        "avoid_when": (
            "Default floor/public layout path; never invent raw colours; hold off until FE live."
        ),
    },
    "costSim": {
        "props_signature": "{} — client-owned defaults, no server props needed.",
        "renders": "Interactive cost-reduction simulator (sliders, before/after bars, saved/mo).",
        "when_to_use": "Charts band; brief mentions cost, cloud spend, FinOps, infra savings — zero grounding cost.",
        "avoid_when": "No cost/infra signal in the brief.",
    },
    "scene2d": {
        "props_signature": "{preset: 'orbit'|'pulse-grid'|'particle-field', palette?, motion?, title?, caption?} (nodes/edges auto from real projects)",
        "renders": "Declarative animated canvas-2D scene — genuinely visual, not a diagram.",
        "when_to_use": "Visual storytelling adds value and a band (usually Architecture) can afford a livelier block.",
        "avoid_when": "Page already has enough motion (flowAnim) in the same band.",
    },
}


def _build_catalog() -> dict[str, BlockFeature]:
    from plugins.portfolio_plugin.compose.block_builder import (
        _AUTHORED_TYPES,
        _ARCH_TYPE,
        _DB_DERIVED_TYPES,
    )
    from plugins.portfolio_plugin.compose.composer import _DAG_LEVEL_BY_TYPE
    from plugins.portfolio_plugin.schema.ui_layout_schema import BLOCK_TYPES

    widget_types = {"mcpSandbox", "costSim"}
    dual_types = {"chart", _ARCH_TYPE}

    def _grounding(t: str) -> Grounding:
        if t in dual_types:
            return "dual"
        if t in widget_types:
            return "widget"
        if t in _AUTHORED_TYPES:
            return "authored"
        if t in _DB_DERIVED_TYPES:
            return "db"
        return "db"  # unreachable given BLOCK_TYPES/_DB_DERIVED_TYPES parity; safe default

    def _source_refs(t: str) -> SourceRefPolicy:
        if t in dual_types:
            return "required_if_authored"
        if t in _AUTHORED_TYPES:
            return "required"
        return "not_required"

    def _default_span(t: str, level: int) -> int:
        if t == "card":
            return 6
        if level == 6:  # Deep dive — one component per row
            return 12
        return 12

    out: dict[str, BlockFeature] = {}
    for t in sorted(BLOCK_TYPES):
        level, label = _DAG_LEVEL_BY_TYPE.get(t, (9, "More"))
        prose = _PROSE.get(t)
        if prose is None:
            logger.warning("block_catalog: no prose entry for block type %r", t)
            prose = {
                "props_signature": "",
                "renders": "",
                "when_to_use": "Use when it best serves the brief.",
                "avoid_when": "",
            }
        out[t] = BlockFeature(
            type=t,
            band_level=level,
            band_label=label,
            grounding=_grounding(t),
            source_refs=_source_refs(t),
            default_span=_default_span(t, level),
            props_signature=prose.get("props_signature", ""),
            renders=prose.get("renders", ""),
            when_to_use=prose.get("when_to_use", ""),
            avoid_when=prose.get("avoid_when", ""),
        )
    return out


def get_block_catalog() -> dict[str, BlockFeature]:
    """Return the block feature catalog, keyed by block type (sorted build)."""
    return _build_catalog()


def fail_open_block_catalog() -> list[dict[str, object]]:
    """List-of-dicts catalog for public-facing surfaces (design context REST/MCP).

    Never raises — a build failure here must not break ``get_design_context`` /
    ``GET /portfolio/design-context``, which are otherwise pure settings reads.
    """
    try:
        return [block_feature_dict(f) for f in get_block_catalog().values()]
    except Exception as exc:
        logger.warning("block_catalog: fail-open, returning empty catalog: %s", exc)
        return []


def block_feature_dict(feat: BlockFeature) -> dict[str, object]:
    """Serialize one ``BlockFeature`` for prompt/JSON consumers."""
    return {
        "type": feat.type,
        "band": {"level": feat.band_level, "label": feat.band_label},
        "grounding": feat.grounding,
        "source_refs": feat.source_refs,
        "default_span": feat.default_span,
        "props_signature": feat.props_signature,
        "renders": feat.renders,
        "when_to_use": feat.when_to_use,
        "avoid_when": feat.avoid_when,
    }
