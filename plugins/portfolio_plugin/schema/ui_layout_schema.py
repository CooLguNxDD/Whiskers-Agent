"""UI layout schema mirroring CatPortfolio/src/content/schema.ts.

Canonical home: ``plugins/portfolio_plugin/schema/ui_layout_schema.py``.
There is no ``utils.ui_layout_schema`` re-export — import from this module
(or the shorter ``plugins.portfolio_plugin.schema`` package re-export)
directly.

Both schemas must be updated together to maintain frontend alignment.
Additive schema (version stays 1) — new block types + composite DSL + themeOverrides.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

# ── shared primitives ───────────────────────────────────────────────────────

class UILink(BaseModel):
    """Link element representation."""
    label: str
    href: str


class UIStat(BaseModel):
    """Stat element representation."""
    label: str
    value: str


class UISource(BaseModel):
    """Citation for agentically-composed layouts — one grounded context ref."""
    ref: str
    label: str | None = None
    url: str | None = None
    kind: str | None = None


class BlockLayoutHint(BaseModel):
    """Optional layout hints honored by LayoutRenderer (CSS grid)."""
    span: int | None = Field(default=None, ge=1, le=12)
    order: int | None = None


class ChartPoint(BaseModel):
    """Single chart data point."""
    x: str | float | int
    y: float | int


class ChartSeries(BaseModel):
    """Named series for chart blocks / composite chart leaves."""
    name: str
    points: list[ChartPoint] = Field(default_factory=list)


# ── existing blocks ─────────────────────────────────────────────────────────

class HeroProps(BaseModel):
    """Props for HeroBlock."""
    name: str
    tagline: str
    pitch: str | None = None
    links: list[UILink] | None = None


class HeroBlock(BaseModel):
    """Hero section block."""
    type: Literal["hero"] = "hero"
    id: str
    props: HeroProps
    layout: BlockLayoutHint | None = None


class UIProject(BaseModel):
    """Project description inside a ProjectGridBlock."""
    id: str
    name: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    metrics: list[UIStat] = Field(default_factory=list)
    links: list[UILink] = Field(default_factory=list)


class ProjectGridProps(BaseModel):
    """Props for ProjectGridBlock."""
    projects: list[UIProject]


class ProjectGridBlock(BaseModel):
    """Project grid section block."""
    type: Literal["projectGrid"] = "projectGrid"
    id: str
    props: ProjectGridProps
    layout: BlockLayoutHint | None = None


class StatStripProps(BaseModel):
    """Props for StatStripBlock."""
    stats: list[UIStat]


class StatStripBlock(BaseModel):
    """Stats strip section block."""
    type: Literal["statStrip"] = "statStrip"
    id: str
    props: StatStripProps
    layout: BlockLayoutHint | None = None


class StarStoryProps(BaseModel):
    """Props for StarStoryBlock."""
    situation: str
    task: str
    action: str
    result: str
    tags: list[str] = Field(default_factory=list)


class StarStoryBlock(BaseModel):
    """STAR method story section block."""
    type: Literal["starStory"] = "starStory"
    id: str
    props: StarStoryProps
    layout: BlockLayoutHint | None = None


class ArchDiagramProps(BaseModel):
    """Props for ArchDiagramBlock."""
    title: str
    kind: Literal["mermaid", "svg"]
    source: str


class ArchDiagramBlock(BaseModel):
    """Architecture diagram section block."""
    type: Literal["archDiagram"] = "archDiagram"
    id: str
    props: ArchDiagramProps
    layout: BlockLayoutHint | None = None


class CodeSnippetProps(BaseModel):
    """Props for CodeSnippetBlock."""
    lang: str
    code: str
    caption: str | None = None


class CodeSnippetBlock(BaseModel):
    """Code snippet section block."""
    type: Literal["codeSnippet"] = "codeSnippet"
    id: str
    props: CodeSnippetProps
    layout: BlockLayoutHint | None = None


class ProseProps(BaseModel):
    """Props for ProseBlock."""
    markdown: str


class ProseBlock(BaseModel):
    """Prose markdown section block."""
    type: Literal["prose"] = "prose"
    id: str
    props: ProseProps
    layout: BlockLayoutHint | None = None


# ── schema v2 named blocks ──────────────────────────────────────────────────

class ChartProps(BaseModel):
    """Props for ChartBlock."""
    kind: Literal["bar", "line", "area", "donut", "radar"]
    title: str | None = None
    series: list[ChartSeries] = Field(default_factory=list)
    caption: str | None = None
    unit: str | None = None


class ChartBlock(BaseModel):
    """Data visualization block (Recharts + ChartConfig on the frontend)."""
    type: Literal["chart"] = "chart"
    id: str
    props: ChartProps
    layout: BlockLayoutHint | None = None


class TimelineItem(BaseModel):
    """One timeline entry."""
    date: str
    title: str
    body: str | None = None
    tag: str | None = None


class TimelineProps(BaseModel):
    """Props for TimelineBlock."""
    title: str | None = None
    items: list[TimelineItem] = Field(default_factory=list)


class TimelineBlock(BaseModel):
    """Career / project arc timeline."""
    type: Literal["timeline"] = "timeline"
    id: str
    props: TimelineProps
    layout: BlockLayoutHint | None = None


class FlowNode(BaseModel):
    """Node in an animated flow diagram."""
    id: str
    label: str
    group: str | None = None


class FlowEdge(BaseModel):
    """Edge in an animated flow diagram."""
    from_: str = Field(alias="from")
    to: str
    label: str | None = None

    model_config = {"populate_by_name": True}


class FlowAnimProps(BaseModel):
    """Props for FlowAnimBlock."""
    title: str | None = None
    nodes: list[FlowNode] = Field(default_factory=list)
    edges: list[FlowEdge] = Field(default_factory=list)
    animate: bool | None = True


class FlowAnimBlock(BaseModel):
    """Animated system flow (moving counterpart to static archDiagram)."""
    type: Literal["flowAnim"] = "flowAnim"
    id: str
    props: FlowAnimProps
    layout: BlockLayoutHint | None = None


class KpiItem(BaseModel):
    """One KPI cell."""
    label: str
    value: str
    delta: str | None = None
    spark: list[float] | None = None


class KpiGridProps(BaseModel):
    """Props for KpiGridBlock."""
    items: list[KpiItem] = Field(default_factory=list)


class KpiGridBlock(BaseModel):
    """Richer KPI grid than statStrip."""
    type: Literal["kpiGrid"] = "kpiGrid"
    id: str
    props: KpiGridProps
    layout: BlockLayoutHint | None = None


class ComparisonColumn(BaseModel):
    """Column header for comparison tables."""
    label: str


class ComparisonRow(BaseModel):
    """One comparison row."""
    label: str
    cells: list[str] = Field(default_factory=list)


class ComparisonProps(BaseModel):
    """Props for ComparisonBlock."""
    title: str | None = None
    columns: list[ComparisonColumn] = Field(default_factory=list)
    rows: list[ComparisonRow] = Field(default_factory=list)


class ComparisonBlock(BaseModel):
    """Before/after or tradeoff comparison table."""
    type: Literal["comparison"] = "comparison"
    id: str
    props: ComparisonProps
    layout: BlockLayoutHint | None = None


class QuickAction(BaseModel):
    """Visitor CTA chip that seeds chat with a prompt."""
    label: str
    prompt: str
    icon: str | None = None


class QuickActionsProps(BaseModel):
    """Props for QuickActionsBlock."""
    prompt: str | None = None
    actions: list[QuickAction] = Field(default_factory=list)


class QuickActionsBlock(BaseModel):
    """Visitor quick-action chips for chat seeding."""
    type: Literal["quickActions"] = "quickActions"
    id: str
    props: QuickActionsProps
    layout: BlockLayoutHint | None = None


# Accent / domain vocab shared with CatPortfolio schema.ts
ACCENT_IDS = frozenset({"amber", "pink", "neon", "cyan", "violet"})
DOMAIN_IDS = frozenset({"ai", "devops", "mobile", "platform"})


class CardMedia(BaseModel):
    """Optional media for a card block."""
    kind: Literal["image", "svg", "icon"] | None = None
    src: str
    alt: str | None = None


class CardBadge(BaseModel):
    """Verified chip on a matrix card."""
    label: str
    href: str | None = None
    tone: Literal["neon", "amber"] | None = None


class CardProps(BaseModel):
    """Generic portfolio card — first-class unit for matrix tiles / agent emit."""
    title: str | None = None
    eyebrow: str | None = None
    body: str | None = None
    media: CardMedia | None = None
    metrics: list[UIStat] | None = None
    tags: list[str] | None = None
    links: list[UILink] | None = None
    badges: list[CardBadge] | None = None
    tech: str | None = None
    domain: Literal["ai", "devops", "mobile", "platform"] | None = None
    accent: Literal["amber", "pink", "neon", "cyan", "violet"] | None = None
    variant: Literal["solid", "outline", "ghost"] | None = None


class CardBlock(BaseModel):
    """Card section block (domain-tinted matrix tile or free-form card)."""
    type: Literal["card"] = "card"
    id: str
    props: CardProps
    layout: BlockLayoutHint | None = None


class McpSandboxBlock(BaseModel):
    """Client-only mock MCP sandbox interactive (matrix L3)."""
    type: Literal["mcpSandbox"] = "mcpSandbox"
    id: str
    props: dict[str, Any] = Field(default_factory=dict)
    layout: BlockLayoutHint | None = None


class CostSimBlock(BaseModel):
    """Client-only AWS cost simulator interactive (matrix L4)."""
    type: Literal["costSim"] = "costSim"
    id: str
    props: dict[str, Any] = Field(default_factory=dict)
    layout: BlockLayoutHint | None = None


# ── scene2d (Phase 6b) ──────────────────────────────────────────────────────

SCENE2D_PRESETS = ("orbit", "pulse-grid", "particle-field")


class Scene2dMotion(BaseModel):
    """Animation tuning for a scene2d block -- no arbitrary paths/expressions,
    just bounded numeric knobs the frontend rAF loop reads."""
    speed: float = Field(default=1.0, ge=0.1, le=3.0)
    loop: bool = True
    intensity: float = Field(default=1.0, ge=0.0, le=2.0)


class Scene2dProps(BaseModel):
    """Props for Scene2dBlock -- preset + grounded data, not a general
    drawing DSL (mirrors flowAnim: nodes/edges come from the builder, never
    invented by the agent). ``renderer`` is fixed "2d" today; the Literal is
    a union deliberately left open for "webgl" later so adding it is a
    schema widening, not a rewrite -- three.js is NOT added until that path
    ships (~600KB with no bundle-splitting config in CatPortfolio yet)."""
    renderer: Literal["2d"] = "2d"
    preset: Literal["orbit", "pulse-grid", "particle-field"]
    title: str | None = None
    nodes: list[FlowNode] = Field(default_factory=list)
    edges: list[FlowEdge] = Field(default_factory=list)
    palette: Literal["amber", "pink", "neon", "cyan", "violet"] | None = None
    motion: Scene2dMotion = Field(default_factory=Scene2dMotion)
    caption: str | None = None


class Scene2dBlock(BaseModel):
    """Declarative canvas-2D visual (matrix L4, alongside mcpSandbox/costSim)."""
    type: Literal["scene2d"] = "scene2d"
    id: str
    props: Scene2dProps
    layout: BlockLayoutHint | None = None


# ── fishTank (WebGL aquarium) ───────────────────────────────────────────────

DOMAIN_IDS = ("ai", "devops", "mobile", "platform")


class FishSpecimen(BaseModel):
    """One project-as-fish. All floats are clamped 0..1 so a bad generation
    makes an ugly tank, never a broken page."""
    slug: str
    title: str
    species: Literal["ai", "devops", "mobile", "platform"] = "platform"
    size: float = Field(default=0.5, ge=0.0, le=1.0)
    depth: float = Field(default=0.5, ge=0.0, le=1.0)
    speed: float = Field(default=0.5, ge=0.0, le=1.0)
    glow: float = Field(default=0.3, ge=0.0, le=1.0)
    school: int = Field(default=0, ge=0, le=15)
    tags: list[str] = Field(default_factory=list)
    blurb: str | None = None
    description: str | None = None
    detailRef: str | None = None
    link: UILink | None = None
    metrics: list[UIStat] = Field(default_factory=list)
    # Fractional-year project timeline (e.g. 2025.67), omitted (never null)
    # when the project has no known date — see compose/fish.py _tank_time_span.
    # endYear omitted means ongoing.
    startYear: float | None = None
    endYear: float | None = None


class TankTimeSpan(BaseModel):
    """Fractional-year bounds across all dated fish, for client-side band mapping."""
    min: float
    max: float


class FishTankProps(BaseModel):
    """Props for FishTankBlock — flat specimen list, no raw colours."""
    renderer: Literal["webgl"] = "webgl"
    title: str | None = None
    fish: list[FishSpecimen] = Field(default_factory=list, max_length=40)
    tankTheme: str | None = None
    cameraFocus: str | None = None
    highlightSlugs: list[str] = Field(default_factory=list)
    curationLabel: str | None = None
    palette: Literal["amber", "pink", "neon", "cyan", "violet"] | None = None
    caption: str | None = None
    timeSpan: TankTimeSpan | None = None


class FishTankBlock(BaseModel):
    """WebGL aquarium scene (matrix L3 Architecture)."""
    type: Literal["fishTank"] = "fishTank"
    id: str
    props: FishTankProps
    layout: BlockLayoutHint | None = None


# ── composite DSL ───────────────────────────────────────────────────────────

COMPOSITE_LEAF_KINDS = frozenset({
    "metric", "sparkline", "badgeCloud", "text", "quote", "progress",
    "image", "icon", "divider", "chart",
    "card", "media", "kv", "tagRow", "link", "stat",
})
COMPOSITE_CONTAINER_KINDS = frozenset({"grid", "stack", "split", "cards"})
COMPOSITE_MAX_DEPTH = 3
COMPOSITE_MAX_NODES = 40


class CompositeLayoutSpec(BaseModel):
    """Layout container for composite roots / nested containers."""
    kind: Literal["grid", "stack", "split", "cards"]
    cols: int | None = Field(default=None, ge=1, le=4)
    gap: Literal["sm", "md", "lg"] | None = None
    align: str | None = None


def _count_composite_nodes(nodes: list[Any], depth: int) -> tuple[int, int]:
    """Return (node_count, max_depth_reached) for a composite children tree."""
    if depth > COMPOSITE_MAX_DEPTH:
        return 0, depth
    total = 0
    max_d = depth
    for node in nodes:
        if not isinstance(node, dict):
            continue
        total += 1
        kind = node.get("kind")
        if kind in COMPOSITE_CONTAINER_KINDS:
            kids = node.get("children") or []
            if isinstance(kids, list):
                c, d = _count_composite_nodes(kids, depth + 1)
                total += c
                max_d = max(max_d, d)
        elif kind not in COMPOSITE_LEAF_KINDS and kind is not None:
            # unknown kind still counts as a node; validation rejects later
            pass
    return total, max_d


def _validate_composite_node(node: Any, path: str) -> list[str]:
    """Validate one composite node (leaf or container)."""
    errors: list[str] = []
    if not isinstance(node, dict):
        return [f"{path}: must be an object"]
    kind = node.get("kind")
    if not isinstance(kind, str) or not kind:
        return [f"{path}.kind: required string"]
    if kind in COMPOSITE_CONTAINER_KINDS:
        kids = node.get("children")
        if kids is None:
            kids = []
        if not isinstance(kids, list):
            errors.append(f"{path}.children: must be a list")
        else:
            for i, child in enumerate(kids):
                errors.extend(_validate_composite_node(child, f"{path}.children[{i}]"))
    elif kind in COMPOSITE_LEAF_KINDS:
        pass  # leaf props are free-form strings/numbers; FE degrades unknown fields
    else:
        errors.append(
            f"{path}.kind: unknown '{kind}' "
            f"(allowed containers: {sorted(COMPOSITE_CONTAINER_KINDS)}; "
            f"leaves: {sorted(COMPOSITE_LEAF_KINDS)})"
        )
    return errors


class CompositeProps(BaseModel):
    """Props for CompositeBlock — recursive layout + primitive leaves."""
    title: str | None = None
    layout: CompositeLayoutSpec
    children: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_tree(self) -> CompositeProps:
        count, depth = _count_composite_nodes(self.children, 1)
        if depth > COMPOSITE_MAX_DEPTH:
            raise ValueError(
                f"composite children exceed max depth {COMPOSITE_MAX_DEPTH}"
            )
        if count > COMPOSITE_MAX_NODES:
            raise ValueError(
                f"composite children exceed max nodes {COMPOSITE_MAX_NODES} (got {count})"
            )
        errors: list[str] = []
        for i, child in enumerate(self.children):
            errors.extend(_validate_composite_node(child, f"children[{i}]"))
        if errors:
            raise ValueError("; ".join(errors[:12]))
        return self


class CompositeBlock(BaseModel):
    """Composable visual container of typed primitives (no arbitrary code)."""
    type: Literal["composite"] = "composite"
    id: str
    props: CompositeProps
    layout: BlockLayoutHint | None = None


# ── root layout ─────────────────────────────────────────────────────────────

UIBlock = Annotated[
    Union[
        HeroBlock,
        ProjectGridBlock,
        StatStripBlock,
        StarStoryBlock,
        ArchDiagramBlock,
        CodeSnippetBlock,
        ProseBlock,
        ChartBlock,
        TimelineBlock,
        FlowAnimBlock,
        KpiGridBlock,
        ComparisonBlock,
        QuickActionsBlock,
        CardBlock,
        McpSandboxBlock,
        CostSimBlock,
        Scene2dBlock,
        FishTankBlock,
        CompositeBlock,
    ],
    Field(discriminator="type"),
]


# Theme override keys must match cozy.theme.json vars (frontend CSS custom props).
THEME_VAR_ALLOWLIST = frozenset({
    "bg", "bg-sunken", "bg-elevated", "card", "card-soft",
    "fg", "fg-muted", "fg-subtle",
    "border", "border-strong", "hairline",
    "amber", "amber-soft", "amber-glow", "peach", "pink", "pink-soft",
    "neon", "neon-dim", "cyan",
    "accent-amber", "accent-pink", "accent-neon", "accent-cyan", "accent-violet",
    "accent-ai", "accent-devops", "accent-mobile", "accent-platform",
    "ok", "ok-soft", "warn", "warn-soft", "danger", "danger-soft",
    "term-bg", "term-fg", "term-dim", "term-amber", "term-green", "term-pink", "term-cyan",
    "radius-sm", "radius", "radius-lg",
    "shadow-card", "shadow-pop", "glow-amber", "glow-pink", "glow-neon",
    "font-sans", "font-mono",
    "pad-card", "pad-row", "nav-pad", "row-min", "tab-pad",
})

# Strict value regex — values go into element.style.setProperty (security control).
_THEME_VALUE_RE = re.compile(
    r"^(?:"
    r"oklch\([^)]+\)"
    r"|#[0-9a-fA-F]{3,8}"
    r"|\d+(?:\.\d+)?(?:px|rem|em|%)"
    r"|\"[^\"]{1,80}\""
    r"|'[^\']{1,80}'"
    r"|var\(--[a-z0-9-]+\)"
    r"|rgba?\([^)]+\)"
    r"|[a-zA-Z0-9 ,\-_/]{1,120}"  # font stacks / simple keywords
    r")$"
)


def sanitize_theme_overrides(raw: Any) -> dict[str, str] | None:
    """Drop unknown keys / unsafe values; never inject raw attacker CSS."""
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or k not in THEME_VAR_ALLOWLIST:
            continue
        if not isinstance(v, str):
            continue
        val = v.strip()
        if not val or not _THEME_VALUE_RE.match(val):
            continue
        out[k] = val
    return out or None


class DagLevel(BaseModel):
    """One horizontal band in the Open Design matrix level-row DAG."""
    level: int = Field(ge=0)
    label: str
    at: float | None = Field(default=None, ge=0, le=1)
    nodes: list[str] = Field(default_factory=list)
    # Peer columns in this band (1–4). Omit = min(4, node count). Use 1 for deep-dive.
    cols: int | None = Field(default=None, ge=1, le=4)


class DagMeta(BaseModel):
    """Level-row story DAG metadata (frontend LayoutRenderer groups by this)."""
    levels: list[DagLevel] = Field(default_factory=list)


class UILayoutMeta(BaseModel):
    """Metadata for the UI layout."""
    audience: Literal["recruiter", "hiring-manager", "peer", "default"] = "default"
    generatedAt: str
    # Optional vibe lever for agentic design_layout; frontend maps to theme registry.
    theme: str | None = None
    # Global accent axis (re-points --amber on FE); amber is default / omit.
    accent: Literal["amber", "pink", "neon", "cyan", "violet"] | None = None
    # Validated CSS-var overrides (allowlisted keys + strict value regex).
    themeOverrides: dict[str, str] | None = None
    # Aggregated citations from agentically-composed blocks (build_layout_block).
    sources: list[UISource] | None = None
    # GenUI / agentic chrome signals (optional; omit for plain snapshots).
    mode: str | None = None
    curationLabel: str | None = None
    scopedProjectCount: int | None = None
    # Open Design matrix: level-row DAG (optional; FE falls back to stagger).
    dag: DagMeta | None = None
    # Job-bake framing (bake_portfolio_for_job / job_tailor) — optional.
    jobCompany: str | None = None
    jobRole: str | None = None
    jobBriefHash: str | None = None
    tailored: bool | None = None
    contentFingerprint: str | None = None
    # Provenance from compose paths (agentic bake stamps these).
    composePath: str | None = None
    structureMode: str | None = None
    recipeId: str | None = None
    juryComposite: float | None = None
    evidencePackHash: str | None = None
    # Evidence pack is planner context only — counts/hash, never raw stores.
    evidenceContextOnly: bool | None = None
    evidenceProjectCount: int | None = None
    evidenceDocCount: int | None = None
    evidenceIndexCount: int | None = None
    planSource: str | None = None
    enrichment: str | None = None
    patchedBlockIds: list[str] | None = None
    # JD-scored curation slugs (bake stamps these; fish tank + card order read
    # them). Declared here because an undeclared key is silently dropped by
    # ``model_dump`` — every ``validate_layout`` round-trip (patch, ask overlay)
    # would otherwise erase fish curation.
    highlightSlugs: list[str] | None = None
    # Nested free-mode stamp from layout_agent (counts only).
    evidence: dict[str, Any] | None = None
    structureClone: bool | None = None

    @field_validator("themeOverrides", mode="before")
    @classmethod
    def _sanitize_overrides(cls, v: Any) -> dict[str, str] | None:
        return sanitize_theme_overrides(v)


class UILayout(BaseModel):
    """Root UI layout configuration."""
    version: Literal[1]
    meta: UILayoutMeta
    blocks: list[UIBlock]


BLOCK_TYPES = frozenset({
    "hero",
    "projectGrid",
    "statStrip",
    "starStory",
    "archDiagram",
    "codeSnippet",
    "prose",
    "chart",
    "timeline",
    "flowAnim",
    "kpiGrid",
    "comparison",
    "quickActions",
    "card",
    "mcpSandbox",
    "costSim",
    "composite",
    "scene2d",
    "fishTank",
})


def filter_unknown_blocks(data: Any) -> Any:
    """Filter out blocks of unknown type before validation."""
    if not isinstance(data, dict):
        return data
    if "blocks" not in data or not isinstance(data["blocks"], list):
        return data

    filtered_blocks = []
    for block in data["blocks"]:
        if isinstance(block, dict) and block.get("type") in BLOCK_TYPES:
            filtered_blocks.append(block)

    result = data.copy()
    result["blocks"] = filtered_blocks
    return result


def validate_layout(data: Any) -> tuple[dict | None, list[str]]:
    """Validate data against UILayout after filtering unknown blocks."""
    filtered_data = filter_unknown_blocks(data)
    try:
        model = UILayout.model_validate(filtered_data)
        return model.model_dump(mode="json", exclude_none=True, by_alias=True), []
    except ValidationError as e:
        errors = []
        for error in e.errors():
            loc_path = ".".join(str(x) for x in error["loc"])
            errors.append(f"{loc_path}: {error['msg']}")
        return None, errors
