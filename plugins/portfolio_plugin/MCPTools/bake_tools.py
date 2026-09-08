"""
Portfolio bake tools — job-tailored layout artifacts owned by portfolio_plugin.

job_search_plugin stays focused on search / evaluate / apply tracking; this
module owns "bake & send": compose a layout with full portfolio context
(live context_sources refresh, optional fragments, audience inference) and
persist under a short URL-safe id for resume PDF + public ``?j=`` links.

This module is the MCP tool surface. The pipeline it drives lives beside it in
``bake/``: ``job_signals`` (widen the JD), ``compose_flow`` (the compose ladder),
``persist`` (short-id allocation), ``contract`` (the single quality bar) and
``run_context`` (error/timing accumulation). ``list_bake_runs`` is in
``bake_admin_tools``.
"""

from __future__ import annotations

import logging
from typing import Any

from core.context import mcp, current_tenant_id
from plugins.portfolio_plugin.store import (
    create_job_layout,
    get_job_layout_by_short_id,
    update_job_layout,
)
from plugins.portfolio_plugin.compose.composer import (
    compose_layout,
    infer_audience_from_job_signals,
)
from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout
from plugins.portfolio_plugin.bake.run_context import (
    BakeContext,
    BakeErrorCode,
    Timer,
)
from plugins.portfolio_plugin.bake.telemetry import new_run_id, record_bake_run

# Split out of this module; re-exported so existing importers keep working.
from plugins.portfolio_plugin.bake.job_signals import (  # noqa: F401
    _call_plugin_op,
    resolve_job_posting_text,
)
from plugins.portfolio_plugin.bake.compose_flow import (  # noqa: F401
    _compose_job_layout,
    _stamp_compose_meta,
)
from plugins.portfolio_plugin.bake.persist import (  # noqa: F401
    MAX_SHORT_ID_ATTEMPTS,
    allocate_short_id,
)

logger = logging.getLogger("whiskers.plugins.portfolio.bake_tools")


def _safe_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.debug("bake: invalid int %r, defaulting to %d", value, default)
        return default


def _safe_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.debug("bake: invalid float %r, defaulting to %f", value, default)
        return default


@mcp.tool(
    title="bake_portfolio_for_job",
    tags={"portfolio_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def bake_portfolio_for_job(
    job_description: str,
    company: str,
    role: str,
    job_application_job_id: str = "",
    provider: str = "",
    posting_url: str = "",
    theme: str = "",
    use_fragments: bool = True,
    layout: dict | None = None,
    display_copy_by_slug: dict | None = None,
) -> dict:
    """Compose and persist a job-tailored portfolio layout; returns its short id.

    Owned by **portfolio_plugin** (not job_search). Default path is **agentic**
    LayoutPlan (recipe seed → plan/materialize → jury), not a fixed L0–L7
    template. Pass ``layout`` to persist an already-composed specialist draft
    without re-thinning. ``portfolio_layout.mode=fast`` uses the **floor
    composer** (not thin scoped compose).

    A fresh short id is minted per call (no idempotency dedupe — re-running
    for the same job just leaves an orphaned, harmless extra row).

    Tenant is always taken from the authenticated principal
    (``current_tenant_id``) — never from a caller-supplied argument — so a
    write-scoped key cannot bake another tenant's projects into the public
    ``/?j=`` layout surface.

    After compose + job tailor, card/fish display copy is rewritten from
    inventory context (never raw ``portfolio_projects.summary`` dumps). Optional
    ``display_copy_by_slug`` from FlowSpec ``author_display`` stage seeds that
    rewrite; bake still dump-guards and re-authors when the stage is skipped.

    Args:
        job_description: Free-text posting body (minimum signal).
        company: Employer name (short_id seed + web_search soft signal).
        role: Role title (short_id seed + inference weight).
        job_application_job_id: Optional board/application id for live detail fetch.
        provider: Job board provider for ``get_job_details`` (optional).
        posting_url: Public posting URL for ``fetch_url`` (optional).
        theme: Optional layout theme id (cozy|neon|paper|latte|frappe|macchiato|mocha).
        use_fragments: Ignored (kept for wire back-compat; fragments removed).
        layout: Optional prebuilt layout dict (specialist draft) to persist as-is.
        display_copy_by_slug: Optional precomputed dual-granularity blobs from
            ``author_project_display_copy`` (FlowSpec stage).
    """
    if not company or not str(company).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["company"],
        }
    if not role or not str(role).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["role"],
        }

    # Fail closed: principal tenant only (no caller override, no silent tenant-1).
    raw_tid = current_tenant_id.get()
    try:
        tid = int(raw_tid) if raw_tid is not None else 0
    except (TypeError, ValueError):
        tid = 0
    if tid <= 0:
        return {
            "status": "error",
            "error": "missing_tenant_context",
            "message": "Authenticated tenant context is required to bake a portfolio layout.",
        }

    ctx = BakeContext(company=str(company), role=str(role), tenant_id=tid)
    run_id = new_run_id()

    resolved = await resolve_job_posting_text(
        job_description or "",
        job_application_job_id=job_application_job_id,
        provider=provider,
        posting_url=posting_url,
        company=company,
        ctx=ctx,
    )
    if not resolved.strip():
        await record_bake_run(ctx, run_id=run_id, status="error", degraded=True)
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["job_description"],
            "message": "Provide a job_description and/or posting_url/provider detail.",
            "run_id": run_id,
        }

    # use_fragments retained on the MCP signature for wire back-compat only.
    _ = use_fragments
    layout, audience, star_query, errors = await _compose_job_layout(
        resolved_text=resolved,
        company=company,
        role=role,
        tenant_id=tid,
        theme=theme,
        prebuilt_layout=layout if isinstance(layout, dict) else None,
        ctx=ctx,
    )
    if layout is None:
        logger.error(
            "bake: no layout for %s/%s run_id=%s codes=%s",
            company,
            role,
            run_id,
            ",".join(ctx.codes()) or "-",
        )
        await record_bake_run(ctx, run_id=run_id, status="error", degraded=True)
        return {
            "status": "error",
            "error": "layout_compose_failed",
            "run_id": run_id,
            "errors": errors,
            "timings_ms": ctx.timings(),
            "total_ms": ctx.total_ms,
        }

    # Always job-frame the page so two JDs cannot ship as the same inventory
    # hero/titles (compose still uses static SETTINGS.hero + shared project rows).
    try:
        from plugins.portfolio_plugin.compose.job_tailor import tailor_layout_for_job

        tailored = tailor_layout_for_job(
            layout if isinstance(layout, dict) else None,
            company=str(company or ""),
            role=str(role or ""),
            job_text=resolved,
            audience=str(audience or ""),
        )
        if isinstance(tailored, dict) and tailored.get("blocks"):
            layout = tailored
    except Exception as exc:
        logger.warning("bake: job_tailor fail-open: %s", exc)
        ctx.add_exc("tailor", BakeErrorCode.TAILOR_FAILED, exc)

    # Rewrite card bodies that dump inventory summary, then optional fishTank.
    # Choke point for all bake entry points (FlowSpec + PortfolioAgent_run +
    # direct MCP). Caps live in manifest settings.display_copy.
    try:
        from plugins.portfolio_plugin.store import list_projects
        from plugins.portfolio_plugin.compose.job_tailor import job_tokens
        from plugins.portfolio_plugin.compose.display_copy import (
            rewrite_layout_project_copy,
        )

        projects = await list_projects(tenant_id=tid) or []
        tokens = job_tokens(str(company or ""), str(role or ""), resolved)
        if isinstance(layout, dict):
            layout, bodies = rewrite_layout_project_copy(
                layout,
                projects if isinstance(projects, list) else [],
                job_tokens=tokens,
                display_copy_by_slug=(
                    display_copy_by_slug
                    if isinstance(display_copy_by_slug, dict)
                    else None
                ),
            )
            ctx.stamp(displayCopyRewritten=True)
        else:
            bodies = {}
    except Exception as exc:
        logger.warning("bake: display_copy rewrite fail-open: %s", exc)
        projects = []
        tokens = set()
        bodies = {}

    # Optional fishTank append (default off — FE must be live before flip).
    try:
        from plugins.portfolio_plugin.plugin_config import SETTINGS

        if bool(SETTINGS.get("fish_tank_enabled")) and isinstance(layout, dict):
            from plugins.portfolio_plugin.compose.fish import build_fish_tank_block
            from plugins.portfolio_plugin.compose.dag import stamp_dag_from_blocks
            from plugins.portfolio_plugin.compose.job_tailor import matched_project_slugs

            if not projects:
                from plugins.portfolio_plugin.store import list_projects as _lp

                projects = await _lp(tenant_id=tid) or []
            if not tokens:
                from plugins.portfolio_plugin.compose.job_tailor import job_tokens as _jt

                tokens = _jt(str(company or ""), str(role or ""), resolved)

            meta = layout.get("meta") if isinstance(layout.get("meta"), dict) else {}
            # Curation is computed here, not in job_tailor: highlight slugs must
            # be real project slugs, and only the project rows carry those.
            hl = matched_project_slugs(
                projects if isinstance(projects, list) else [],
                tokens,
            )
            # Trim roster to JD-relevant projects, keeping explicit highlights; empty JD yields full inventory.
            fish_cfg = SETTINGS.get("fish_tank") or {}
            roster_limit = _safe_int(fish_cfg.get("job_roster_limit"), 8)
            min_relevance = _safe_float(fish_cfg.get("min_relevance"), 2.0)

            # Prefer post-rewrite card bodies; fish author dump-guards preferred.
            tank = build_fish_tank_block(
                projects if isinstance(projects, list) else [],
                highlight_slugs=[str(s) for s in hl if s],
                block_id="fish-tank-1",
                curation_label=str(meta.get("curationLabel") or "") or None,
                tank_theme=str(meta.get("theme") or "") or None,
                job_tokens=tokens,
                body_by_slug=bodies or None,
                roster_limit=roster_limit,
                min_relevance=min_relevance,
            )
            if tank:
                blocks = list(layout.get("blocks") or [])
                # Replace existing fishTank if present; else append.
                blocks = [b for b in blocks if not (isinstance(b, dict) and b.get("type") == "fishTank")]
                blocks.append(tank)
                dag = stamp_dag_from_blocks(blocks)
                meta2 = dict(meta)
                if dag:
                    meta2["dag"] = dag
                if hl:
                    meta2["highlightSlugs"] = hl
                layout = {**layout, "blocks": blocks, "meta": meta2}
                fish_count = len(tank.get("props", {}).get("fish") or [])
                ctx.stamp(
                    fishTank=True,
                    fishCount=fish_count,
                    fishRosterFallback=bool(tokens) and fish_count > roster_limit,
                )
    except Exception as exc:
        logger.warning("bake: fish_tank append fail-open: %s", exc)
        ctx.add_exc("fish_tank", BakeErrorCode.FISH_TANK_FAILED, exc)

    # --- Quality gate ---------------------------------------------------------
    # Runs *after* tailor + fishTank: those mutate blocks and meta after the
    # only previous validation, so anything they produced used to reach the
    # public route unchecked. Nothing is minted or persisted below this line
    # until the layout clears the contract.
    _bake_meta = layout.get("meta") if isinstance(layout, dict) else {}
    compose_path = str(
        ctx.provenance.get("composePath") or (_bake_meta or {}).get("composePath") or ""
    )
    mode = str(ctx.provenance.get("mode") or (_bake_meta or {}).get("mode") or "")

    from plugins.portfolio_plugin.bake.contract import assess_bake_quality
    from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config

    _cfg = get_portfolio_layout_config()
    quality = assess_bake_quality(
        layout,
        goal_class="bake_for_job",
        agent_result={
            "ship_best": bool(ctx.provenance.get("shipBest")),
            "jury": {"composite": ctx.provenance.get("juryComposite")},
            "jury_history": [None] * int(ctx.provenance.get("juryRounds") or 0),
        },
        cfg=_cfg,
    )
    ctx.stamp(
        qualityPassed=quality.passed,
        qualityViolations=quality.codes() or None,
    )

    degraded = (
        bool(ctx.errors)
        or not quality.passed
        or mode.startswith("degraded")
        or compose_path in {"floor", "floor_fast", "audience_template"}
    )

    if not quality.passed:
        hard_fail = bool(_cfg.get("hard_fail_on_quality"))
        logger.warning(
            "bake: quality gate %s for %s/%s run_id=%s violations=%s",
            "FAILED (no row persisted)" if hard_fail else "failed (soak: shipping anyway)",
            company,
            role,
            run_id,
            ",".join(v.code for v in quality.blocking),
        )
        if hard_fail:
            await record_bake_run(
                ctx,
                run_id=run_id,
                status="error",
                compose_path=compose_path or None,
                mode=mode or None,
                degraded=True,
                job_brief_hash=(_bake_meta or {}).get("jobBriefHash"),
            )
            return {
                "status": "error",
                "error": "quality_gate_failed",
                "run_id": run_id,
                "quality": quality.to_dict(),
                "compose_path": compose_path or None,
                "errors": ctx.error_dicts(),
                "timings_ms": ctx.timings(),
                "total_ms": ctx.total_ms,
            }

    short_id = await allocate_short_id(company, role)

    if short_id is None:
        logger.error("Failed to generate a unique portfolio short_id for %s/%s", company, role)
        ctx.add_error(
            "persist",
            BakeErrorCode.SHORT_ID_COLLISION,
            f"exhausted {MAX_SHORT_ID_ATTEMPTS} attempts",
            fatal=True,
        )
        await record_bake_run(ctx, run_id=run_id, status="error", degraded=True)
        return {
            "status": "error",
            "error": "short_id_collision",
            "run_id": run_id,
            "errors": ctx.error_dicts(),
            "timings_ms": ctx.timings(),
            "total_ms": ctx.total_ms,
        }

    # Durable provenance: recipeId/juryComposite are stamped into layout.meta by
    # _stamp_compose_meta on the agentic/recipe-skeleton paths; surface them onto
    # the row so "what did we ship for this job" is queryable without
    # deserializing layout_json. plan_json now rides on the bake context.
    await create_job_layout(
        short_id=short_id,
        layout=layout,
        audience=audience,
        star_query=star_query,
        job_application_job_id=job_application_job_id or None,
        tenant_id=tid,
        recipe_id=(_bake_meta or {}).get("recipeId"),
        jury_score=(_bake_meta or {}).get("juryComposite"),
        plan_json=ctx.plan,
        compose_path=compose_path or None,
        mode=mode or None,
        degraded=degraded,
    )

    await record_bake_run(
        ctx,
        run_id=run_id,
        status="ok",
        short_id=short_id,
        compose_path=compose_path or None,
        mode=mode or None,
        degraded=degraded,
        job_brief_hash=(_bake_meta or {}).get("jobBriefHash"),
    )

    logger.info(
        "bake: ok short_id=%s path=%s mode=%s degraded=%s total_ms=%s codes=%s",
        short_id,
        compose_path,
        mode,
        degraded,
        ctx.total_ms,
        ",".join(ctx.codes()) or "-",
    )

    return {
        "status": "ok",
        "short_id": short_id,
        # Alias of short_id — job_search_plugin's auto-bake dispatch and the
        # career_ops_apply_v1 FlowSpec both read portfolio_job_id (the public
        # ?j= param name), never short_id directly. Additive only; short_id
        # stays the primary key for portfolio-internal callers.
        "portfolio_job_id": short_id,
        "query_param": f"j={short_id}",
        "audience": audience,
        "star_query": star_query,
        "theme": (layout.get("meta") or {}).get("theme") if isinstance(layout, dict) else None,
        "plugin": "portfolio_plugin",
        "run_id": run_id,
        # A bake can succeed having degraded through several rungs — surface the
        # causes on success too, or "ok" hides every fallback that got us here.
        "degraded": degraded,
        "compose_path": compose_path or None,
        "quality": quality.to_dict(),
        "errors": ctx.error_dicts(),
        "timings_ms": ctx.timings(),
        "total_ms": ctx.total_ms,
        # Include layout so Ask/chat can re-render immediately without a second fetch.
        "layout": layout,
    }


@mcp.tool(
    title="patch_job_layout",
    tags={"portfolio_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def patch_job_layout(
    base_short_id: str,
    sections: list[dict] | None = None,
    patch_mode: str = "append_or_update",
    derived_short_id: str = "",
) -> dict:
    """Merge 1–2 blocks into a job layout and persist a **derived** fork.

    The original HR-facing bake (``?j=<base_short_id>``) is **never** mutated.
    First call mints a new derived short_id (``parent_short_id=base``);
    later turns pass that derived id as ``derived_short_id`` to update the fork.

    Prefer for chat turns that already have a demo short_id in session:
    ``search_portfolio_context`` → ``build_layout_block`` × 1–2 → this tool.
    Do not re-call ``bake_portfolio_for_job`` for incremental chat patches.

    Args:
        base_short_id: Original bake short_id (or current derived id when forking
            is already done — used as parent for a fresh derived row).
        sections: Block dicts from ``build_layout_block`` (1–2 recommended).
        patch_mode: ``append_or_update`` (default), ``update_only``, or ``replace``.
        derived_short_id: When set, update that derived row only (refuses
            non-derived ids so forged/stale ids cannot rewrite an HR bake).
    """
    from plugins.portfolio_plugin.compose.context_enrich import enrich_layout_dict
    from plugins.portfolio_plugin.compose.patch import compose_patch_layout

    if not base_short_id or not str(base_short_id).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["base_short_id"],
        }
    if not isinstance(sections, list) or len(sections) == 0:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["sections"],
            "message": "Pass 1–2 block dicts from build_layout_block as sections.",
        }

    raw_tid = current_tenant_id.get()
    try:
        tid = int(raw_tid) if raw_tid is not None else 0
    except (TypeError, ValueError):
        tid = 0
    if tid <= 0:
        return {
            "status": "error",
            "error": "missing_tenant_context",
            "message": "Authenticated tenant context is required to patch a job layout.",
        }

    load_id = (derived_short_id or base_short_id).strip()
    row = await get_job_layout_by_short_id(load_id)
    if row is None and derived_short_id and str(derived_short_id).strip():
        # Stale derived id — fall back to base for a fresh fork.
        row = await get_job_layout_by_short_id(str(base_short_id).strip())
        derived_short_id = ""
    if row is None:
        return {
            "status": "error",
            "error": "not_found",
            "message": f"No job layout for short_id={load_id!r}",
        }
    if row.get("tenant_id") is not None:
        try:
            if int(row["tenant_id"]) != tid:
                return {
                    "status": "error",
                    "error": "tenant_mismatch",
                    "message": "Layout belongs to another tenant.",
                }
        except (TypeError, ValueError):
            return {"status": "error", "error": "tenant_mismatch"}

    base_layout = row.get("layout_json")
    if not isinstance(base_layout, dict):
        return {
            "status": "error",
            "error": "invalid_layout",
            "message": "Stored layout_json is not an object.",
        }

    parent_id = str(base_short_id).strip()
    # If we loaded a derived row without explicit derived_short_id, keep parent chain.
    if row.get("is_derived") and row.get("parent_short_id"):
        parent_id = str(row.get("parent_short_id") or parent_id)

    patch_result = await compose_patch_layout(
        {"sections": sections, "audience": row.get("audience")},
        tenant_id=tid,
        base_layout=base_layout,
        patch_mode=patch_mode or "append_or_update",
    )
    if patch_result.get("status") != "ok" or not isinstance(patch_result.get("layout"), dict):
        return {
            "status": "error",
            "error": "patch_compose_failed",
            "errors": patch_result.get("errors") or [],
            "section_errors": patch_result.get("section_errors") or [],
            "warnings": patch_result.get("warnings") or [],
        }

    layout = await enrich_layout_dict(
        patch_result["layout"],
        tenant_id=tid,
        preserve_dag=True,
    )
    if not isinstance(layout, dict):
        layout = patch_result["layout"]

    target_derived = str(derived_short_id or "").strip()
    # Re-patching an already-derived session: update in place even when the FE
    # only passes the session id as base_short_id (no separate derived arg).
    if not target_derived and row.get("is_derived") and row.get("short_id"):
        target_derived = str(row["short_id"])

    if target_derived:
        ok = await update_job_layout(target_derived, layout=layout, tenant_id=tid)
        if not ok:
            return {
                "status": "error",
                "error": "update_refused",
                "message": (
                    "update_job_layout refused — id missing, wrong tenant, or not a "
                    "derived row (original bakes are immutable)."
                ),
                "short_id": target_derived,
            }
        short_id = target_derived
    else:
        seed = parent_id.split("_")[:2] or [parent_id, "patch"]
        short_id = await allocate_short_id(*seed, "patch")
        if short_id is None:
            return {"status": "error", "error": "short_id_collision"}
        await create_job_layout(
            short_id=short_id,
            layout=layout,
            audience=str(row.get("audience") or "default"),
            star_query=row.get("star_query"),
            job_application_job_id=row.get("job_application_job_id"),
            tenant_id=tid,
            parent_short_id=parent_id,
            is_derived=True,
        )

    return {
        "status": "ok",
        "short_id": short_id,
        "query_param": f"j={short_id}",
        "parent_short_id": parent_id,
        "is_derived": True,
        "layout": layout,
        "patched_block_ids": patch_result.get("patched_block_ids") or [],
        "section_errors": patch_result.get("section_errors") or [],
        "warnings": patch_result.get("warnings") or [],
        "plugin": "portfolio_plugin",
    }


@mcp.tool(
    title="author_project_display_copy",
    tags={"portfolio_plugin"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def author_project_display_copy(
    company: str = "",
    role: str = "",
    job_description: str = "",
    layout: dict | None = None,
) -> dict[str, Any]:
    """Author dual-granularity project display copy from inventory context.

    FlowSpec stage (``author_display``): produces ``display_copy_by_slug`` for
    bake to seed fish/card rewrite. Inventory ``summary`` is seed only — never
    returned as a raw dump. When ``layout`` is provided, also returns a layout
    with inventory-dump card bodies rewritten.

    Caps: ``settings.display_copy`` in the portfolio plugin manifest.
    """
    from plugins.portfolio_plugin.compose.display_copy import (
        build_display_copy_by_slug,
        rewrite_layout_project_copy,
    )
    from plugins.portfolio_plugin.compose.job_tailor import job_tokens
    from plugins.portfolio_plugin.store import list_projects
    from plugins.portfolio_plugin.tenant import PORTFOLIO_TENANT_ID, require_tenant_id

    tid = require_tenant_id() or PORTFOLIO_TENANT_ID
    projects = await list_projects(tenant_id=int(tid)) or []
    tokens = job_tokens(str(company or ""), str(role or ""), str(job_description or ""))
    blobs = build_display_copy_by_slug(
        projects if isinstance(projects, list) else [],
        job_tokens=tokens,
    )
    out: dict[str, Any] = {
        "status": "ok",
        "display_copy_by_slug": blobs,
        "project_count": len(blobs),
    }
    if isinstance(layout, dict):
        rewritten, _ = rewrite_layout_project_copy(
            layout,
            projects if isinstance(projects, list) else [],
            job_tokens=tokens,
            display_copy_by_slug=blobs,
        )
        out["layout"] = rewritten
    return out


@mcp.tool(
    title="resolve_bake_job_signals",
    tags={"portfolio_plugin"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def resolve_bake_job_signals(
    goal: str = "",
    job_description: str = "",
    company: str = "",
    role: str = "",
) -> dict[str, Any]:
    """Extract company/role/job_description from explicit fields or free text.

    Thin wrapper over ``agents.bake._parse_job_signals`` — the same
    lightweight "bake portfolio for ROLE at COMPANY" regex parse
    ``run_bake_agent`` already uses. Exists so a declarative ``FlowSpec``'s
    first stage can resolve job signals from a natural-language goal before
    the ``bake`` stage dispatches ``bake_portfolio_for_job`` (which requires
    company/role as structured fields, not free text) — without this stage,
    a triage-originated goal like "bake portfolio for Engineer at Acme" would
    reach the bake stage with no company/role and fail closed for no reason.
    """
    from plugins.portfolio_plugin.agents.bake import _parse_job_signals

    sig = _parse_job_signals(
        goal or job_description,
        {"company": company or None, "role": role or None, "job_description": job_description or None},
    )
    missing = [k for k in ("company", "role") if not sig.get(k)]
    if missing:
        return {
            "status": "error",
            "error": "missing_job_signals",
            "missing_fields": missing,
            "message": (
                "Bake needs a company and role. Pass them explicitly or phrase the "
                "goal as 'bake portfolio for <role> at <company>'."
            ),
        }
    return {"status": "ok", **sig}


@mcp.tool(
    title="assess_bake_quality",
    tags={"portfolio_plugin"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def assess_bake_quality(
    layout: dict[str, Any],
    goal_class: str = "bake_for_job",
    jury: dict[str, Any] | None = None,
    agent_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the single bake quality contract against a layout.

    Thin MCP wrapper over ``bake.contract.assess_bake_quality`` — the same
    function ``bake_portfolio_for_job`` calls internally. Exists so a
    declarative ``FlowSpec`` deterministic stage (see
    ``core_graph.subgraphs.specialist.flow_spec``) can dispatch it as an
    ordinary op instead of re-implementing a quality bar. Never a second
    quality bar: this *is* the one bar, just independently callable.

    Args:
        layout: The GenUI layout dict to assess.
        goal_class: "bake_for_job" or "redesign" — affects band-coverage bar.
        jury: Optional pre-computed jury dict ({"composite": float}).
        agent_result: Optional run_layout_agent envelope (ship_best, jury_history).
    """
    from plugins.portfolio_plugin.bake.contract import assess_bake_quality
    from plugins.portfolio_plugin.layout.layout_config import get_portfolio_layout_config

    cfg = get_portfolio_layout_config()
    report = assess_bake_quality(
        layout, goal_class=goal_class, jury=jury, agent_result=agent_result, cfg=cfg
    )
    return {"status": "ok", "passed": report.passed, **report.to_dict()}
