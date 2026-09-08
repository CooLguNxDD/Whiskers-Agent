"""Design system + craft planes for Portfolio Layout Engine prompts."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.design_system")

_ROOT = Path(__file__).resolve().parent.parent  # plugins/portfolio_plugin
_DS_ROOT = _ROOT / "design_systems"
_SKILLS_ROOT = _ROOT / "skills"


@lru_cache(maxsize=8)
def load_design_system(name: str = "default") -> dict[str, Any]:
    """Load DESIGN.md-lite package: USAGE.md, DESIGN.md, tokens.json, settings.json."""
    import json

    pkg = _DS_ROOT / (name or "default")
    if not pkg.is_dir():
        pkg = _DS_ROOT / "default"
    out: dict[str, Any] = {
        "id": name or "default",
        "usage_md": "",
        "design_md": "",
        "tokens": {},
        "settings": {},
        "path": str(pkg) if pkg.is_dir() else "",
    }
    if not pkg.is_dir():
        return out
    for key, filename in (
        ("usage_md", "USAGE.md"),
        ("design_md", "DESIGN.md"),
    ):
        fp = pkg / filename
        if fp.is_file():
            try:
                out[key] = fp.read_text(encoding="utf-8")[:12000]
            except Exception as exc:
                logger.debug("design system read %s: %s", fp, exc)
    tokens_path = pkg / "tokens.json"
    if tokens_path.is_file():
        try:
            data = json.loads(tokens_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out["tokens"] = data
        except Exception as exc:
            logger.debug("tokens.json: %s", exc)
    settings_path = pkg / "settings.json"
    if settings_path.is_file():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out["settings"] = data
                # Prefer design_tokens from settings when present
                dt = data.get("design_tokens")
                if isinstance(dt, dict) and dt:
                    out["design_tokens"] = dt
        except Exception as exc:
            logger.debug("settings.json: %s", exc)
    return out


def clear_design_system_cache() -> None:
    """Clear in-memory LRU caches for design system configurations and craft modules."""
    load_design_system.cache_clear()
    load_craft_module.cache_clear()


@lru_cache(maxsize=16)
def load_craft_module(name: str) -> str:
    """Deprecated: craft/ folded into skills; returns empty for back-compat."""
    return ""


def load_craft_modules(names: list[str] | tuple[str, ...] | None = None) -> dict[str, str]:
    """Deprecated: craft/ folded into skills; returns empty dict."""
    return {}


#: design_systems/*/tokens.json keys -> plugins.portfolio_plugin.schema.ui_layout_schema.THEME_VAR_ALLOWLIST keys.
#: The allowlist holds unprefixed FE CSS-var names (cozy.theme.json vars); tokens.json
#: authors write "--accent"/"--surface"/"colors.domain-*" convention names instead. A
#: plain "--" strip does NOT bridge these (stripped "accent"/"surface" still aren't in
#: the allowlist) — without this alias map every themeOverride is silently dropped by
#: sanitize_theme_overrides and the whole design-token -> CSS-var lever is inert.
_TOKEN_ALIASES: dict[str, str] = {
    "accent": "amber",
    "accent-soft": "amber-soft",
    "surface": "bg-elevated",
    "surface-sunken": "bg-sunken",
    "text": "fg",
    "text-muted": "fg-muted",
    "colors-domain-ai": "accent-ai",
    "colors-domain-devops": "accent-devops",
    "colors-domain-mobile": "accent-mobile",
    "colors-domain-platform": "accent-platform",
}


def tokens_to_theme_overrides(tokens: dict[str, Any] | None) -> dict[str, str]:
    """Map design-system tokens.json -> CSS-var themeOverrides (string values only).

    Normalizes each token key (strip leading "--", "_" -> "-", lowercase),
    applies ``_TOKEN_ALIASES``, then sanitizes at the producer via
    ``sanitize_theme_overrides`` so callers can trust the result is already
    allowlisted -- never returns keys/values that would be silently dropped
    downstream.
    """
    if not isinstance(tokens, dict):
        return {}
    raw: dict[str, str] = {}
    # Flat map: {"--accent": "..."} or nested {"colors": {"accent": "..."}}
    for k, v in tokens.items():
        if isinstance(v, str) and k.startswith("--"):
            raw[k] = v
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if isinstance(v2, str):
                    key = k2 if str(k2).startswith("--") else f"--{k}-{k2}".replace("_", "-")
                    raw[key] = v2
        elif isinstance(v, str):
            key = k if k.startswith("--") else f"--{k}"
            raw[key] = v

    aliased: dict[str, str] = {}
    for k, v in raw.items():
        norm = k.lstrip("-").replace("_", "-").lower()
        aliased[_TOKEN_ALIASES.get(norm, norm)] = v

    from plugins.portfolio_plugin.schema.ui_layout_schema import sanitize_theme_overrides

    return sanitize_theme_overrides(aliased) or {}


def propose_directions(brief: str = "", *, theme_hint: str = "") -> list[dict[str, str]]:
    """OD-style direction picker: 3 fixed vibes (auto-pick later by brief)."""
    directions = [
        {
            "id": "neon-systems",
            "theme": "neon",
            "density": "deep",
            "lede": "Systems engineer deep dive — architecture-first, cyan accents.",
        },
        {
            "id": "paper-recruiter",
            "theme": "paper",
            "density": "light",
            "lede": "Recruiter skim — hero, proof cards, clear CTA.",
        },
        {
            "id": "cozy-story",
            "theme": "cozy",
            "density": "story",
            "lede": "Narrative STAR-led portfolio with warm proof bands.",
        },
    ]
    q = (brief or "").lower()
    # Light ranking for auto-pick
    scores = []
    for d in directions:
        s = 0
        if theme_hint and d["theme"] == theme_hint:
            s += 5
        if any(k in q for k in ("recruiter", "hire", "hr", "resume")) and d["id"] == "paper-recruiter":
            s += 3
        if any(k in q for k in ("architecture", "platform", "systems", "reliability", "mcp")) and d[
            "id"
        ] == "neon-systems":
            s += 3
        if any(k in q for k in ("story", "star", "narrative")) and d["id"] == "cozy-story":
            s += 3
        scores.append((s, d))
    scores.sort(key=lambda x: -x[0])
    # Return all three; first is recommended
    ordered = [d for _, d in scores]
    ordered[0] = {**ordered[0], "recommended": "true"}
    return ordered


def auto_pick_direction(brief: str = "", *, theme_hint: str = "") -> dict[str, str]:
    """Select the highest-scoring layout design direction based on brief keywords and theme hints."""
    dirs = propose_directions(brief, theme_hint=theme_hint)
    return dirs[0] if dirs else {"id": "neon-systems", "theme": "neon", "density": "deep"}


def compose_layout_system_prompt(
    *,
    design_system_id: str = "default",
    craft_names: list[str] | None = None,
    recipe: dict[str, Any] | None = None,
    skill_body: str = "",
    evidence_budget: str = "",
    harness_block: str = "",
    structure_mode: str = "recipe_seed",
    block_catalog: str = "",
) -> str:
    """OD-ordered prompt planes: USAGE → DESIGN → tokens → craft → recipe → harness → skill → evidence.

    ``structure_mode=free`` demotes recipe skeleton (quality/theme only), injects
    the GenUI block catalog, and treats evidence as the primary project truth.
    """
    free = str(structure_mode or "").strip().lower() == "free"
    ds = load_design_system(design_system_id)
    craft = load_craft_modules(craft_names or (recipe or {}).get("craft"))
    parts: list[str] = [
        "# Portfolio Layout Agent",
        "You plan GenUI layouts as LayoutPlan JSON (validated blocks only — never free HTML).",
        "Materialization is server-side. Cite source_refs for every authored block.",
    ]
    if free:
        parts.append(
            "## STRUCTURE MODE: FREE (context-first)\n"
            "You own page structure from the full GenUI catalog "
            "(portfolio_plugin schema).\n"
            "``portfolio_projects`` + ``portfolio_plugin__context`` are **CONTEXT ONLY** "
            "for choosing what to build — never dump the inventory as the page, and never "
            "treat either store as the final result payload.\n"
            "Do **not** copy a fixed recipe page. Mix cards, timeline, comparison, "
            "flowAnim, chart, prose, composite, codeSnippet freely when evidence supports them. "
            "Prefer explicit project `slugs`. Authored prose/composite/chart claims must cite "
            "evidence refs. `top_k` only caps items **inside** a block, never the whole page shape.\n"
            "Deep dive must use a distinct context-doc excerpt — never reuse the same "
            "project.summary as both card body and prose for the same slug.\n"
            "starStory only with authored grounded S/T/A/R props + source_refs "
            "(portfolio_star is retired).\n"
            "Projects band density: cards use `layout.span: 6` and "
            "`band: {level: 2, label: Projects, cols: 2}` (two cards per row)."
        )
    if ds.get("usage_md"):
        parts.append("## USAGE\n" + ds["usage_md"][:8000])
    if ds.get("design_md"):
        parts.append("## DESIGN SYSTEM\n" + ds["design_md"][:5000])
    if ds.get("tokens"):
        parts.append(f"## TOKENS\n```json\n{ds['tokens']}\n```")
    for name, body in craft.items():
        if body:
            parts.append(f"## CRAFT · {name}\n{body[:2500]}")
    if block_catalog and free:
        parts.append("## BLOCK CATALOG\n" + block_catalog[:4500])
    if recipe:
        if free:
            parts.append(
                "## RECIPE (quality + theme only — not a page template)\n"
                f"id={recipe.get('id')} default_theme={recipe.get('default_theme')}\n"
                f"quality={recipe.get('quality')}\n"
                "Do not replay any skeleton step list. Invent structure from catalog + evidence."
            )
        else:
            parts.append(
                "## SELECTED RECIPE\n"
                f"id={recipe.get('id')} theme={recipe.get('default_theme')}\n"
                f"skeleton={recipe.get('skeleton')}\n"
                f"quality={recipe.get('quality')}\n"
            )
    if harness_block:
        # Inserted after the recipe, before the skill: memory reads as a
        # refinement of the recipe skeleton, not a competing instruction.
        parts.append(harness_block)
    if skill_body:
        parts.append("## SKILL\n" + skill_body[:8000])
    if evidence_budget:
        parts.append("## EVIDENCE (budgeted)\n" + evidence_budget[:8000])
    parts.append(_COMPOSITE_DSL_PLANE)
    parts.append(_VISUALS_PLANE)
    if free:
        output_plane = (
            "## OUTPUT\n"
            "Emit a LayoutPlan object matching the output schema — that plan is the "
            "result, not the raw project/context stores. "
            "You choose block_types, order, bands, and spans from the catalog. "
            "Vary structure by job signals (AI vs mobile vs DevOps emphasis). "
            "Use authored prose/composite only with real source_refs from EVIDENCE. "
            "Steps may set `band: {level, label, cols?}` and `layout: {span, order}`. "
            "Spread blocks across matrix bands; the jury penalizes >60% in one band "
            "and penalizes cloning a fixed recipe skeleton. "
            "Include meta.dag levels for full-page bake/redesign."
        )
    else:
        output_plane = (
            "## OUTPUT\n"
            "Emit a LayoutPlan object matching the output schema. "
            "Prefer diverse block_types; use authored prose/composite only with real source_refs "
            "from search. Steps may set `band: {level, label, cols?}` to place blocks in a "
            "specific matrix band directly, instead of relying on the type->band default — "
            "spread blocks across bands rather than crowding one band; the jury penalizes "
            ">60% of blocks in a single band. Include meta.dag levels for full redesign pages."
        )
    if harness_block:
        output_plane += (
            " When LAYOUT MEMORY shows the seed skeleton scored below threshold for a "
            "similar brief, restructure rather than replay it."
        )
    parts.append(output_plane)
    return "\n\n".join(parts)


# Static -- svg_render.MOTIFS' keys, spelled out so the agent doesn't have to
# call a tool just to discover the vocabulary. Kept as a literal list (not a
# live import of svg_render.MOTIFS) to avoid a runtime import here; the
# authoritative list is svg_render.py and test_portfolio_svg_render.py locks
# each one to real rendering behavior.
_VISUALS_PLANE = """## VISUALS

archDiagram with `kind: "svg"` + `props.motif` renders a generated SVG
grounded in real project data (Phase 6a) instead of mermaid: `timeline`
(dated milestones), `stack` (layered request-path diagram), `metric_ring`
(donut gauges from headline project metrics), `topology` (radial node graph
of your projects). No `props.source` needed -- the server derives it from
DB projects. Prefer this over mermaid when the content is genuinely visual
(a metric, a request path, a timeline) rather than a flowchart."""

# Static plane -- the DSL itself doesn't vary per design system/recipe, unlike
# the other planes above, so it's a module constant rather than a parameter.
_COMPOSITE_DSL_PLANE = """## COMPOSITE DSL

`composite` is the one general-purpose primitive block: a small recursive
layout tree (containers + leaves), not free HTML. Use it when no named block
fits, instead of inventing a new block type.

props: { title?, layout: {kind: "grid"|"stack"|"split"|"cards", cols?: 1-4, gap?, align?}, children: [...] }

Containers (kind + children, same shape recursively): grid, stack, split, cards.
Leaves (kind + free-form props): metric, sparkline, badgeCloud, text, quote,
progress, image, icon, divider, chart, card, media, kv, tagRow, link, stat.

Caps (server-enforced): depth <= 3, <= 40 total nodes.

Grounding: source_refs required as for any authored block, PLUS proportional
citation -- more authored text-bearing leaf content (text/quote/kv.value/
stat.value/metric.value/badgeCloud.items) needs more refs; one ref cannot
cover a whole authored composite. image/media/link src/href/url must be
http(s):// (no relative paths, no data URIs) -- these render on a public
HR-facing page.

Example (two-column proof band):
{"type": "composite", "id": "proof-1", "props": {
  "title": "Reliability at scale",
  "layout": {"kind": "grid", "cols": 2, "gap": "md"},
  "children": [
    {"kind": "metric", "label": "Uptime", "value": "99.98%"},
    {"kind": "text", "value": "Multi-tenant MCP platform serving..."}
  ]
}, "source_refs": ["github:org/repo"]}"""
