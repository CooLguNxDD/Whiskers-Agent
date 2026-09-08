"""Compose/tailor orchestration for the bake pipeline.

The fall-through ladder that turns resolved job signals into a layout: agentic
compose, recipe skeleton, scoped compose, then the audience template floor.
Every rung is fail-open and stamps its ``composePath``/``mode`` onto the layout
meta, so a degraded result is never mistaken for a clean one.

The quality bar lives in ``bake/contract.py`` and is applied by the caller —
never re-implement it here.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.compose.composer import (
    compose_layout,
    infer_audience_from_job_signals,
)
from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout
from plugins.portfolio_plugin.bake.run_context import BakeContext, BakeErrorCode, Timer

logger = logging.getLogger("whiskers.plugins.portfolio.bake_tools")


def _stamp_compose_meta(
    layout: dict[str, Any] | None,
    *,
    compose_path: str,
    mode: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(layout, dict):
        return layout
    meta = dict(layout.get("meta") or {})
    meta["composePath"] = compose_path
    if mode:
        meta["mode"] = mode
    if extra:
        for k, v in extra.items():
            if v is not None:
                meta[k] = v
    return {**layout, "meta": meta}


async def _compose_job_layout(
    *,
    resolved_text: str,
    company: str,
    role: str,
    tenant_id: int,
    theme: str = "",
    prebuilt_layout: dict[str, Any] | None = None,
    ctx: BakeContext | None = None,
) -> tuple[dict[str, Any] | None, str, str | None, list[dict[str, Any]]]:
    """Build a job-tailored layout using full portfolio context.

    Prefer **agentic LayoutPlan** (Open Design–style: brief → plan → materialize
    → jury). Recipes are seeds, not a fixed L0–L7 page. When
    ``portfolio_layout.mode == fast`` (or agentic fails), uses the floor
    composer — not thin scoped compose.

    Every rung is fail-open, so the returned ``errors`` are how a caller learns
    which rungs declined and why — a degraded layout is indistinguishable from a
    clean one otherwise. Errors accumulate into ``ctx`` when one is supplied;
    otherwise a throwaway context keeps the function usable standalone.

    Returns ``(layout, audience, star_query, errors)`` where each error is a
    ``StageError.to_dict()``.
    """
    from plugins.portfolio_plugin.compose.composer import get_audience_template
    from plugins.portfolio_plugin.layout.layout_config import (
        get_portfolio_layout_config,
        use_agentic_layout,
    )

    ctx = ctx if ctx is not None else BakeContext(company=company, role=role, tenant_id=tenant_id)

    # Weight inference with company/role tokens so STAR query stays on-topic.
    signal = f"{resolved_text}\n\nCompany: {company}\nRole: {role}".strip()
    audience, star_query = infer_audience_from_job_signals(signal)
    audience_n, template = get_audience_template(audience)
    if not star_query:
        star_query = template.get("star_query")

    # Specialist draft already composed a layout — persist, do not re-thin.
    if isinstance(prebuilt_layout, dict) and isinstance(prebuilt_layout.get("blocks"), list):
        blocks = prebuilt_layout.get("blocks") or []
        if len(blocks) >= 3:
            layout, verrs = validate_layout(prebuilt_layout)
            if layout and not verrs:
                stamped = _stamp_compose_meta(
                    layout,
                    compose_path="prebuilt_draft",
                    mode=str((layout.get("meta") or {}).get("mode") or "agentic"),
                )
                meta0 = (stamped or {}).get("meta")
                aud = (
                    str(meta0.get("audience") or audience_n)
                    if isinstance(meta0, dict)
                    else audience_n
                )
                ctx.stamp(composePath="prebuilt_draft")
                return stamped, aud, star_query, ctx.error_dicts()
            logger.info("bake: prebuilt layout invalid, recomposing: %s", verrs)
            ctx.add_error(
                "prebuilt",
                BakeErrorCode.PREBUILT_INVALID,
                "; ".join(str(e) for e in (verrs or []))[:400],
            )

    cfg = get_portfolio_layout_config()
    agentic = use_agentic_layout("bake_for_job", cfg)

    # Pre-bake multi-store context pack (planner input only — never tool output).
    pack: dict[str, Any] | None = None
    ev_timer = Timer()
    try:
        from plugins.portfolio_plugin.layout.evidence_pack import build_evidence_pack

        pack = await build_evidence_pack(
            signal,
            tenant_id=int(tenant_id),
            top_k_docs=int(cfg.get("evidence_top_k_docs") or 28),
            top_k_projects=int(cfg.get("evidence_top_k_projects") or 24),
            web_enrich=bool(cfg.get("web_enrich", True)),
            company=str(company or ""),
            role=str(role or ""),
        )
        inv = (pack or {}).get("inventory") if isinstance(pack, dict) else {}
        logger.info(
            "bake: evidence pack hash=%s projects=%s context_docs=%s index≈%s (context_only)",
            (pack or {}).get("pack_hash"),
            (inv or {}).get("project_count"),
            (inv or {}).get("context_docs_in_pack"),
            (inv or {}).get("context_index_count"),
        )
        ctx.stamp(evidencePackHash=(pack or {}).get("pack_hash"))
    except Exception as exc:
        logger.warning("bake: evidence pack fail-open: %s", exc)
        ctx.add_exc("evidence", BakeErrorCode.EVIDENCE_PACK_FAILED, exc)
        pack = None
    ctx.mark("evidence", ev_timer.ms)

    # --- Primary: agentic LayoutPlan (recipe seed → agent/plan → materialize → jury)
    if agentic:
        ag_timer = Timer()
        try:
            from plugins.portfolio_plugin.agents.layout_agent import run_layout_agent

            res = await run_layout_agent(
                draft=None,
                query=signal,
                goal_class="bake_for_job",
                theme=theme or "",
                tenant_id=int(tenant_id),
                refresh=True,
                company=str(company or ""),
                role=str(role or ""),
                structure_mode="free",
                evidence_pack=pack,
            )
            if isinstance(res, dict) and res.get("layout"):
                layout = res["layout"]
                if isinstance(layout, dict) and layout.get("blocks"):
                    mode = str(res.get("mode") or "layout_plan")
                    if res.get("status") not in ("ok",) and res.get("jury"):
                        # Partial / jury-failed still usable if blocks exist
                        mode = mode if mode != "template" else "degraded_agentic"
                    compose_path = (
                        "context_first_layout_agent"
                        if str(res.get("structure_mode") or "") == "free"
                        else "agentic_layout_agent"
                    )
                    inv = (pack or {}).get("inventory") if isinstance(pack, dict) else {}
                    stamped = _stamp_compose_meta(
                        layout,
                        compose_path=compose_path,
                        mode=mode,
                        extra={
                            "recipeId": res.get("recipe_id"),
                            "juryComposite": (res.get("jury") or {}).get("composite")
                            if isinstance(res.get("jury"), dict)
                            else None,
                            "planSource": res.get("plan_source"),
                            "structureMode": res.get("structure_mode") or "free",
                            "evidencePackHash": res.get("evidence_pack_hash")
                            or (pack or {}).get("pack_hash"),
                            "evidenceContextOnly": True,
                            "evidenceProjectCount": (inv or {}).get("project_count"),
                            "evidenceDocCount": (inv or {}).get("context_docs_in_pack"),
                            "evidenceIndexCount": (inv or {}).get("context_index_count"),
                        },
                    )
                    meta0 = (stamped or {}).get("meta")
                    aud = (
                        str(meta0.get("audience") or audience_n)
                        if isinstance(meta0, dict)
                        else audience_n
                    )
                    logger.info(
                        "bake: agentic layout path status=%s blocks=%s",
                        res.get("status"),
                        len((stamped or {}).get("blocks") or []),
                    )
                    # ship_best means the jury never passed — the layout is a
                    # best loser, not an approved page. Record it as a cause so
                    # a silently degraded bake is queryable before the quality
                    # gate starts rejecting them outright.
                    if res.get("ship_best"):
                        ctx.add_error(
                            "agentic",
                            BakeErrorCode.AGENTIC_JURY_NEVER_PASSED,
                            f"shipped best loser after {len(res.get('jury_history') or [])} round(s)",
                        )
                    ctx.stamp(
                        composePath=compose_path,
                        mode=mode,
                        planSource=res.get("plan_source"),
                        recipeId=res.get("recipe_id"),
                        juryComposite=(res.get("jury") or {}).get("composite")
                        if isinstance(res.get("jury"), dict)
                        else None,
                        shipBest=bool(res.get("ship_best")) or None,
                        juryRounds=len(res.get("jury_history") or []) or None,
                    )
                    # Carried on the context, not the return tuple: the durable
                    # plan_json column is what makes "which plan shipped"
                    # answerable, and the 4-tuple has callers that mock it.
                    if isinstance(res.get("plan"), dict):
                        ctx.plan = res["plan"]
                    ctx.mark("agentic", ag_timer.ms)
                    return stamped, aud, star_query, ctx.error_dicts()
            ctx.add_error(
                "agentic",
                BakeErrorCode.AGENTIC_EMPTY,
                f"status={(res or {}).get('status') if isinstance(res, dict) else type(res).__name__}",
            )
        except Exception as exc:
            logger.warning("bake: agentic layout_agent fail-open: %s", exc)
            ctx.add_exc("agentic", BakeErrorCode.AGENTIC_FAILED, exc)
        ctx.mark("agentic", ag_timer.ms)

        # Deterministic floor composer (replaces recipe/fragments/scoped rungs)
        fl_timer = Timer()
        try:
            from plugins.portfolio_plugin.compose.floor import build_floor_layout

            floor = await build_floor_layout(
                signal,
                tenant_id=int(tenant_id),
                audience=audience_n,
                theme=theme or "",
                refresh=True,
            )
            if isinstance(floor, dict) and floor.get("blocks"):
                stamped = _stamp_compose_meta(
                    floor,
                    compose_path="floor",
                    mode="degraded_floor",
                    extra={"planSource": "floor"},
                )
                logger.warning("bake: using floor composer (agent unavailable/empty)")
                ctx.stamp(composePath="floor", mode="degraded_floor")
                ctx.mark("floor", fl_timer.ms)
                return stamped, audience_n, star_query, ctx.error_dicts()
            ctx.add_error("floor", BakeErrorCode.FLOOR_EMPTY, "floor composer produced no blocks")
        except Exception as exc:
            logger.warning("bake: floor composer fail-open: %s", exc)
            ctx.add_exc("floor", BakeErrorCode.FLOOR_FAILED, exc)
        ctx.mark("floor", fl_timer.ms)
    else:
        # mode=fast (agentic disabled): floor composer is the only compose
        # rung tried before the audience-template fallback. When agentic WAS
        # attempted, its own floor rung above already ran with identical args
        # (signal/tenant_id/audience_n/theme) — repeating it here would just
        # reproduce the same failure, so it is skipped and we fall straight
        # through to the audience template.
        ff_timer = Timer()
        try:
            from plugins.portfolio_plugin.compose.floor import build_floor_layout

            floor = await build_floor_layout(
                signal,
                tenant_id=int(tenant_id),
                audience=audience_n,
                theme=theme or "",
                refresh=True,
            )
            if isinstance(floor, dict) and floor.get("blocks"):
                stamped = _stamp_compose_meta(floor, compose_path="floor_fast", mode="floor")
                ctx.stamp(composePath="floor_fast", mode="floor")
                ctx.mark("floor_fast", ff_timer.ms)
                return stamped, audience_n, star_query, ctx.error_dicts()
            ctx.add_error("floor_fast", BakeErrorCode.FLOOR_EMPTY, "floor composer produced no blocks")
        except Exception as exc:
            logger.warning("bake: floor_fast fail-open: %s", exc)
            ctx.add_exc("floor_fast", BakeErrorCode.FLOOR_FAILED, exc)
        ctx.mark("floor_fast", ff_timer.ms)

    tpl_timer = Timer()
    try:
        layout = await compose_layout(
            audience=audience_n,
            star_query=star_query,
            tenant_id=tenant_id,
            refresh=True,
        )
    except Exception as exc:
        # Last rung: nothing below it, so a raise here means the bake produced
        # no layout at all. Previously this propagated as an opaque tool crash.
        logger.warning("bake: audience template fail-open: %s", exc)
        ctx.add_exc("template", BakeErrorCode.TEMPLATE_FAILED, exc, fatal=True)
        ctx.mark("template", tpl_timer.ms)
        return None, audience_n, star_query, ctx.error_dicts()
    if theme and str(theme).strip() and isinstance(layout, dict):
        meta = layout.get("meta")
        if isinstance(meta, dict):
            meta = dict(meta)
            meta["theme"] = str(theme).strip()
            layout = {**layout, "meta": meta}
    layout = _stamp_compose_meta(
        layout if isinstance(layout, dict) else None,
        compose_path="audience_template",
        mode="degraded_template",
    )
    if not (isinstance(layout, dict) and layout.get("blocks")):
        ctx.add_error(
            "template",
            BakeErrorCode.TEMPLATE_EMPTY,
            "audience template produced no blocks",
            fatal=True,
        )
    ctx.stamp(composePath="audience_template", mode="degraded_template")
    ctx.mark("template", tpl_timer.ms)
    return layout, audience_n, star_query, ctx.error_dicts()
