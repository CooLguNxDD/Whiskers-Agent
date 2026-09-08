---
name: layout-plan-authoring
description: Author LayoutPlan.steps[] for job-bake and redesign goals from evidence + GenUI catalog.
goal_classes: [bake_for_job, redesign]
triggers: [bake, job layout, redesign, full layout, matrix, tailor for, for the job, short_id]
default_theme: neon
quality: {min_blocks: 6, min_types: 5, require_dag: true, forbid_template_mode: true}
target_blocks: 12
target_types: 8
---

# Layout plan authoring (bake / redesign)

You author a **LayoutPlan** with `steps[]`. Each step has `block_type`, optional
`props`, `source_refs`, `slugs`, `query`, `top_k`, and `layout.span` / `layout.order`.
Server materialization builds validated GenUI blocks — never free HTML.

You have **full freedom** over page structure within the GenUI catalog
(`plugins/portfolio_plugin/schema/ui_layout_schema.py` / CatPortfolio `BLOCK_TYPES`).
Pick any combination of catalog types that serves the brief — not a fixed template.

## Structure mode (free)

RAG and project rows are **evidence**, not the page skeleton. Brainstorm ~14–16
candidates from the **entire** block catalog, then ship **10–14** high-value
blocks across ≥7 types and ≥4 DAG bands. Do **not** copy a fixed
hero→kpi→cards→arch sequence unless evidence is truly thin.

Lean into project context:
- One `card` step with `top_k` covering ranked projects (server expands to tiles).
- Add **timeline**, **comparison**, **flowAnim**, **chart**, **prose**, **composite**
  when evidence supports them — one deep-dive prose/composite per strong project is good.
- Prefer explicit `slugs` on cards and deep-dive steps.

## Grounding

- No metrics without project/index source.
- Authored blocks (`prose`, `composite`, `codeSnippet`, `comparison`, custom chart series) **require** `source_refs`.
- Call `search_portfolio_context` first; only cite refs it returned or project `context_sources`.
- Visitor-facing claims must match indexed README/docs — never invent employers, headcount, or commit totals.
- Prefer multi-project coverage on job bakes (domain diversity over one employer family).

## Anti-hallucination

- Class F fields (numeric KPIs, chart points, hrefs) are DB-derived — do not invent them.
- Class G text (title/body) needs verified `source_refs` or the server falls back to DB text.
- Class S visual/structure (span, accent, variant, band cols) may be authored freely.

## Matrix bands (L0–L7)

Intro → Impact → **Projects (2 cards per row)** → Flow/Arch → Charts → Proof → Deep dive → CTA.

- Projects band: always `layout.span: 6` on cards and `band: {level: 2, label: "Projects", cols: 2}`.
  Never pack 4 dense cards in one row.
- Deep dive: `layout.span: 12` / one component per row (`band.cols: 1` or type default).

## Layout rhythm

- Target **12 blocks / 8 types** (floor is lower — aim high when evidence is rich).
- Vary `layout.span` (12 / **6+6** / 4+4+4) — avoid full-width card walls.
- Prefer domain `card` tiles over a single `projectGrid` dump.
- Use under-used catalog types when they fit: `timeline`, `comparison`, `mcpSandbox`,
  `scene2d`, `costSim`, `codeSnippet`, `composite`. Full per-type context (band,
  grounding, props signature, when/avoid) is in the BLOCK CATALOG plane of this
  prompt — it's generated from `schema/block_catalog.py`, don't re-derive it here.
- **Bake coverage targets** (jury rewards these, `structure` dim penalizes their
  absence once ≥3 projects are scoped): ship **≥1 interactive widget**
  (`mcpSandbox` and/or `costSim` — zero grounding cost, always safe to include),
  **≥1 grounded chart-family block** (`chart` or `comparison` with real data —
  never a title-only shell; empty `rows`/`series`/`items` is rejected outright),
  and **≥1 deep-dive** (`composite` or `codeSnippet`) when evidence supports it.

## Coverage failures are visible, not silent

A step that fails to build (bad `source_refs`, unresolvable grounding, an empty
authored shell) is surfaced back to you as `step_failed: ...` must-fix feedback
on the next round — it is never dropped without a trace. If a round's jury
feedback includes one, don't repeat the same step; either fix the grounding or
pick a different block for that slot.

## How you will be graded (jury)

- **brief_fit**: page text addresses JD keywords (nested props count).
- **evidence**: authored blocks cite real refs; multi-project coverage.
- **structure**: block count, type diversity, DAG bands, 2-col projects, anti floor-clone.
- **schema_craft** / **voice_brand**: valid GenUI + precise systems voice.

## Tools

- `search_portfolio_context`, `build_layout_block`, `compose_scoped_layout`,
  `critique_layout`, `get_design_context`, `design_layout` (sections assembly).
- Do **not** call deleted tools: `compose_from_fragments`, `list_layout_recipes`.

## Multi-store context (planner only — not the result)

The evidence pack loads **two stores as CONTEXT only** (never dump them as the page
or as bake tool output):
- `portfolio_projects` inventory (query-ranked)
- `portfolio_plugin__context` discovery index (multi-query RAG)
- plus virtual projects synthesized from index hits not yet in the project table

`portfolio_star` is **retired**. Author `starStory` with grounded S/T/A/R props
+ `source_refs` from the pack, or omit.

Plan GenUI from that context. Prefer diverse slugs/refs. Deep-dive prose must
use a distinct context excerpt — never reuse the same `project.summary` as both
card body and prose.

**Server zero-paste:** floor cards, fish blurbs, and timeline bodies are authored
by `compose/display_copy.author_display_copy` from inventory seed + tags/metrics.
If you paste `project.summary` into a card body, bake rewrites it (dump detection).
Prefer short JD-framed prose with a distinct angle; caps are
`settings.display_copy` in the plugin manifest (`fish_blurb_max_chars` default 200).
FlowSpec `portfolio_bake_v1` runs `author_display` before `bake`.

## Job bake framing

`bake_portfolio_for_job` always runs a deterministic **job tailor** after compose
(`compose/job_tailor.py`): **hero tagline/pitch stay personal brand** (job target
lives in `?j=<short_id>` + silent meta `jobCompany`/`jobRole` only). Cards are
re-ordered by JD lexical score; generic flow/comparison titles use domain cues
without stuffing the full job title. Two employers differ via matching + short_id,
not by rewriting hero copy.

## Incremental patch turn (chat with `?j=` / short_id)

When the visitor already has a demo short_id in session (opened `?j=…` or a
prior bake returned one), **do not** re-bake or full-page recompose:

1. `search_portfolio_context(query)` for evidence
2. `build_layout_block` × **1–2** targeted blocks only
3. `patch_job_layout(base_short_id=<session id>, sections=[…], derived_short_id=<prior derived or empty>)`

The original HR bake is immutable; the first patch mints a **derived** short_id.
Later turns pass that derived id as `derived_short_id`. Put the returned
`layout` + `short_id` in the response so the page updates.

For a dry-run without persisting, `design_layout(spec={base_layout, sections})`
is read-only merge only — chat should prefer `patch_job_layout`.
