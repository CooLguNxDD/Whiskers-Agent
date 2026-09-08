"""LayoutPlan IR + materializer for the Portfolio Layout Engine (PLE).

The agent owns structure via ``LayoutPlan``; the server materializes validated
GenUI blocks (no free HTML). See design: Open Design process, GenUI surface.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger("whiskers.plugins.portfolio.layout_plan")

StepKind = Literal["db", "authored", "auto"]


class LayoutBand(BaseModel):
    """Page-level matrix band a step's produced block(s) belong to.

    Distinct from ``LayoutStep.layout`` ({span,order}), which is a per-block
    grid hint that gets stamped directly onto a block: ``band`` is page-level
    and never reaches a block dict -- it only drives ``meta.dag`` assembly in
    ``compose_scoped_layout`` (accumulated *after* materialization, since a
    step like ``card`` can expand into N builder-generated block ids the plan
    itself can't name in advance).
    """

    model_config = ConfigDict(extra="ignore")

    level: int = Field(..., ge=0)
    label: str = Field(..., min_length=1, max_length=64)
    cols: int | None = Field(default=None, ge=1, le=4)


class LayoutStep(BaseModel):
    """One materialization step → one (or expanded multi) GenUI block(s)."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1, max_length=64)
    kind: StepKind = "authored"
    block_type: str = Field(..., min_length=1, max_length=64)
    query: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)
    slugs: list[str] | None = None
    props: dict[str, Any] | None = None
    source_refs: list[str] | None = None
    layout: dict[str, Any] | None = None  # span / order hints
    band: LayoutBand | None = None  # page-level matrix band (see LayoutBand)

    @field_validator("slugs", "source_refs", mode="before")
    @classmethod
    def _empty_list_ok(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, list):
            return None
        return [str(x).strip() for x in v if str(x).strip()]


class LayoutDirection(BaseModel):
    """Optional visual direction lock (OD-style)."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    label: str | None = None
    theme: str | None = None
    accent: str | None = None
    density: str | None = None
    lede: str | None = None


class LayoutQualityTargets(BaseModel):
    """Quality bar carried with the plan (jury + validator)."""

    model_config = ConfigDict(extra="ignore")

    min_blocks: int = Field(default=3, ge=1, le=40)
    min_types: int = Field(default=2, ge=1, le=20)
    require_citations: bool = False
    forbid_template_mode: bool = True
    require_dag: bool = False


class LayoutPlan(BaseModel):
    """First-class intermediate representation the layout agent owns."""

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    recipe_id: str | None = None
    audience: str = "default"
    theme: str | None = None
    theme_overrides: dict[str, str] | None = None
    direction: LayoutDirection | None = None
    meta: dict[str, Any] | None = None
    steps: list[LayoutStep] = Field(default_factory=list)
    quality_targets: LayoutQualityTargets = Field(default_factory=LayoutQualityTargets)
    brief: str | None = None

    @field_validator("steps", mode="before")
    @classmethod
    def _ensure_steps(cls, v: Any) -> Any:
        return v if isinstance(v, list) else []


def merge_quality_floor(
    recipe_quality: dict[str, Any] | None, plan_quality: dict[str, Any] | None
) -> dict[str, Any]:
    """Floor-merge a recipe's quality bar with the plan's self-declared targets.

    The agent may RAISE the bar via ``LayoutPlan.quality_targets``, never
    lower it: ``LayoutQualityTargets`` defaults (``min_blocks=3``) sit below
    ``min_blocks_for("bake_for_job")`` (5), so a naive "plan wins" merge would
    let the agent quietly downgrade every bake. Counts take ``max()``; boolean
    "require" flags take OR.
    """
    recipe_q = LayoutQualityTargets.model_validate(recipe_quality or {})
    plan_q = LayoutQualityTargets.model_validate(plan_quality or {})
    return {
        "min_blocks": max(recipe_q.min_blocks, plan_q.min_blocks),
        "min_types": max(recipe_q.min_types, plan_q.min_types),
        "require_citations": recipe_q.require_citations or plan_q.require_citations,
        "forbid_template_mode": recipe_q.forbid_template_mode or plan_q.forbid_template_mode,
        "require_dag": recipe_q.require_dag or plan_q.require_dag,
    }


def parse_layout_plan(data: Any) -> LayoutPlan:
    """Parse dict/JSON-ish input into a LayoutPlan; raises ValidationError."""
    if isinstance(data, LayoutPlan):
        return data
    if not isinstance(data, dict):
        raise ValueError("layout plan must be an object")
    return LayoutPlan.model_validate(data)


def plan_to_block_plan(plan: LayoutPlan | dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten LayoutPlan steps into compose_scoped ``block_plan`` dicts."""
    p = plan if isinstance(plan, LayoutPlan) else parse_layout_plan(plan)
    out: list[dict[str, Any]] = []
    for step in p.steps:
        entry: dict[str, Any] = {
            "block_type": step.block_type,
            "block_id": step.id,
        }
        if step.query is not None:
            entry["query"] = step.query
        if step.top_k is not None:
            entry["top_k"] = step.top_k
        if step.slugs:
            entry["slugs"] = list(step.slugs)
        if step.props is not None:
            entry["props"] = dict(step.props)
        if step.source_refs:
            entry["source_refs"] = list(step.source_refs)
        if step.layout:
            entry["layout"] = dict(step.layout)
        # Default kind is authored; only emit non-default for the block_plan wire.
        if step.kind and step.kind not in ("authored",):
            entry["kind"] = step.kind
        if step.band is not None:
            entry["band"] = step.band.model_dump(exclude_none=True)
        out.append(entry)
    return out


def block_plan_to_layout_plan(
    block_plan: list[dict[str, Any]],
    *,
    recipe_id: str | None = None,
    audience: str = "default",
    theme: str | None = None,
    brief: str | None = None,
    quality: dict[str, Any] | None = None,
) -> LayoutPlan:
    """Lift a thin recipe skeleton into a LayoutPlan."""
    steps: list[LayoutStep] = []
    for i, raw in enumerate(block_plan or []):
        if not isinstance(raw, dict):
            continue
        btype = str(raw.get("block_type") or raw.get("type") or "").strip()
        if not btype:
            continue
        bid = str(raw.get("block_id") or raw.get("id") or f"{btype}-{i}")
        kind_raw = str(raw.get("kind") or "authored").strip().lower()
        kind: StepKind = kind_raw if kind_raw in ("db", "authored", "auto") else "authored"
        steps.append(
            LayoutStep(
                id=bid,
                kind=kind,
                block_type=btype,
                query=raw.get("query"),
                top_k=raw.get("top_k"),
                slugs=raw.get("slugs"),
                props=raw.get("props") if isinstance(raw.get("props"), dict) else None,
                source_refs=raw.get("source_refs"),
                layout=raw.get("layout") if isinstance(raw.get("layout"), dict) else None,
                band=raw.get("band") if isinstance(raw.get("band"), dict) else None,
            )
        )
    qt = LayoutQualityTargets.model_validate(quality or {})
    return LayoutPlan(
        recipe_id=recipe_id,
        audience=audience or "default",
        theme=theme,
        steps=steps,
        quality_targets=qt,
        brief=brief,
    )


async def materialize_layout_plan(
    plan: LayoutPlan | dict[str, Any],
    *,
    tenant_id: int = 1,
    query: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Materialize LayoutPlan → validated layout via existing builders.

    Returns ``{status, layout, audience, mode, plan, step_errors?, scoped_project_count?}``.
    Grounding failures on authored steps are collected in ``step_errors`` and
    those steps are skipped (db steps continue).
    """
    from plugins.portfolio_plugin.compose.compose_scoped import compose_scoped_layout

    try:
        p = plan if isinstance(plan, LayoutPlan) else parse_layout_plan(plan)
    except Exception as exc:
        return {
            "status": "error",
            "error": "invalid_layout_plan",
            "message": str(exc)[:500],
        }

    if not p.steps:
        return {
            "status": "error",
            "error": "empty_plan",
            "message": "LayoutPlan has no steps",
        }

    brief = (query or p.brief or "").strip()
    if not brief:
        brief = "portfolio layout"

    block_plan = plan_to_block_plan(p)
    theme = (p.theme or "").strip()
    if p.direction and p.direction.theme and not theme:
        theme = str(p.direction.theme).strip()

    # p.audience defaults to "default" on every plan (LayoutPlan.audience field
    # default), so only forward an explicit non-default choice — otherwise
    # every plan would force-skip compose_scoped_layout's keyword inference
    # even when the agent never actually set an audience.
    plan_audience = (p.audience or "").strip().lower()
    result = await compose_scoped_layout(
        brief,
        tenant_id=int(tenant_id),
        theme=theme,
        refresh=bool(refresh),
        top_k=4,
        block_plan=block_plan,
        audience=plan_audience if plan_audience and plan_audience != "default" else "",
    )

    if not isinstance(result, dict):
        return {"status": "error", "error": "bad_compose_result"}

    layout = result.get("layout")
    if isinstance(layout, dict):
        meta = dict(layout.get("meta") or {})
        if p.theme_overrides:
            existing = meta.get("themeOverrides") if isinstance(meta.get("themeOverrides"), dict) else {}
            merged = {**existing, **{str(k): str(v) for k, v in p.theme_overrides.items()}}
            meta["themeOverrides"] = merged
        if p.recipe_id:
            meta["recipeId"] = p.recipe_id
        if p.direction:
            meta["direction"] = p.direction.model_dump(exclude_none=True)
        if p.meta and isinstance(p.meta, dict):
            # Agent-supplied meta (e.g. dag) wins for keys not already stamped
            for k, v in p.meta.items():
                if k not in meta or meta.get(k) in (None, "", {}, []):
                    meta[k] = v
        if p.audience and not meta.get("audience"):
            meta["audience"] = p.audience
        layout = {**layout, "meta": meta}
        # Project-context enrichment: 2-col cards + extra grounded blocks
        try:
            from plugins.portfolio_plugin.compose.context_enrich import enrich_layout_dict

            enriched = await enrich_layout_dict(layout, tenant_id=int(tenant_id))
            if isinstance(enriched, dict) and enriched.get("blocks"):
                layout = enriched
        except Exception as exc:
            logger.debug("materialize enrich fail-open: %s", exc)
        result = {**result, "layout": layout}

    result["plan"] = p.model_dump(mode="json", exclude_none=True)
    result["materializer"] = "layout_plan"
    return result
