"""MCP tools for ask mode — the visitor-question patch path.

These are the only tools ``portfolio_ask_v1``'s stages dispatch. The routing
decision is a server function, not a prompt: an ask turn cannot reach
``bake_portfolio_for_job`` / ``compose_scoped_layout`` / ``emit_layout`` /
``generate_layout_for_query``, which is what keeps it from full-rebuilding the
page. The overlay itself is still ephemeral — no blocks/DAG/answer text are
persisted — but ``route_portfolio_ask`` now records a durable
``portfolio_ask_turns`` audit row (question/intent/outcome only) via
``ask/telemetry.py``; see that module's docstring.
"""

import logging
import time

from core.context import current_tenant_id, mcp
from plugins.portfolio_plugin.ask.discovery_jobs import (
    await_discovery,
    get_discovery,
    start_discovery,
)
from plugins.portfolio_plugin.ask.fish_pool import take_from_pool
from plugins.portfolio_plugin.ask.overlay import build_ask_overlay as _build_ask_overlay
from plugins.portfolio_plugin.ask.router import AskPlan, plan_from_dict, route_ask
from plugins.portfolio_plugin.ask.spawn import spawn_projects_from_candidates
from plugins.portfolio_plugin.ask.targets import resolve_targets
from plugins.portfolio_plugin.ask.telemetry import (
    mark_ask_turn_failed,
    new_run_id,
    record_ask_turn,
)

logger = logging.getLogger("whiskers.plugins.portfolio")

# Caps how many discovered candidates spawn into the tank in one turn —
# mirrors the pill cap in CatPortfolio's ChatPanel so every spawned fish
# also gets a focus pill.
MAX_DISCOVERY_SPAWN = 4


def _ask_enabled() -> bool:
    from plugins.portfolio_plugin.plugin_config import SETTINGS

    cfg = SETTINGS.get("ask") if isinstance(SETTINGS, dict) else None
    return bool(cfg.get("enabled")) if isinstance(cfg, dict) else False


def _empty_overlay(
    *,
    dag: dict | None,
    highlight_slugs: list,
    pending_job: dict | None,
    warnings: list[str],
    recommendations: list | None = None,
    fish_pool: list | None = None,
    pool_id: str = "",
) -> dict:
    """Answer-only overlay shape — always includes ``pending_job`` for trace."""
    return {
        "status": "ok",
        "blocks": [],
        "patched_block_ids": [],
        "dag": dag,
        "focus_slug": "",
        "highlight_slugs": list(highlight_slugs),
        "recommendations": [dict(r) for r in (recommendations or []) if isinstance(r, dict)],
        "fish_pool": [dict(r) for r in (fish_pool or []) if isinstance(r, dict)],
        "pool_id": str(pool_id or ""),
        "pending_job": pending_job,
        "warnings": list(warnings),
        "errors": [],
    }


@mcp.tool(
    title="route_portfolio_ask",
    tags={"portfolio_plugin", "read", "ask"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def route_portfolio_ask(
    question: str,
    view: str = "text",
    block_index: list[dict] | None = None,
    tank_slugs: list[str] | None = None,
    visitor_session_id: str | None = None,
    add_slugs: list[str] | None = None,
) -> dict:
    """Classify a visitor question into an ask plan — deterministic, no LLM.

    ``block_index`` is the client's ``[{"id","type","slug"?}]`` skeleton of the
    layout currently on screen and ``tank_slugs`` the fish it is showing;
    neither requires shipping a whole layout. Returns ``ask_plan`` with one of
    ``bake`` (hand off to portfolio_bake_v1), ``focus_fish``, ``add_fish``,
    ``discover``, ``patch_blocks``, ``answer_only``, ``recommend``, plus a
    ``run_id`` that
    correlates this turn's audit row — pass it back into ``build_ask_overlay``
    so an overlay failure updates the same row instead of writing a second one.

    ``add_slugs`` short-circuits routing to ``intent="add_fish"`` after the
    router validates each slug against inventory / the visitor fish pool.

    ``visitor_session_id`` is an optional opaque id the client may mint and
    resend across turns (no IP/UA capture — see ask/telemetry.py); there is no
    resolvable authenticated principal on this public path, so it is the only
    "who asked" signal available today.

    Page furniture (hero/kpiGrid/statStrip/quickActions) is never targeted —
    only a real bake owns those.
    """
    if not question or not str(question).strip():
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["question"]}
    if not _ask_enabled():
        return {"status": "error", "error": "ask_disabled", "message": "settings.ask.enabled is false"}

    run_id = new_run_id()
    t0 = time.perf_counter()
    tenant_id = current_tenant_id.get()
    try:
        plan = await route_ask(
            str(question),
            tenant_id=tenant_id,
            view=str(view or "text"),
            block_index=block_index or [],
            tank_slugs=tank_slugs or [],
            add_slugs=add_slugs or [],
            visitor_session_id=visitor_session_id,
        )
    except Exception as exc:
        await record_ask_turn(
            run_id=run_id,
            tenant_id=int(tenant_id or 1),
            question=str(question),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            ok=False,
            visitor_session_id=visitor_session_id,
            view=str(view or "text"),
            error_type=type(exc).__name__,
        )
        raise

    await record_ask_turn(
        run_id=run_id,
        tenant_id=int(tenant_id or 1),
        question=str(question),
        latency_ms=int((time.perf_counter() - t0) * 1000),
        ok=True,
        visitor_session_id=visitor_session_id,
        intent=plan.intent,
        view=str(view or "text"),
        focus_slug=plan.focus_slug or None,
        highlight_slugs=list(plan.highlight_slugs),
        add_slugs=list(plan.add_slugs),
    )
    return {"status": "ok", "ask_plan": plan.to_dict(), "intent": plan.intent, "run_id": run_id}


@mcp.tool(
    title="build_ask_overlay",
    tags={"portfolio_plugin", "read", "ask"},
    annotations={"readOnlyHint": True, "idempotentHint": False},
)
async def build_ask_overlay(
    ask_plan: dict,
    question: str = "",
    block_index: list[dict] | None = None,
    tank_slugs: list[str] | None = None,
    dag: dict | None = None,
    time_span: dict | None = None,
    run_id: str | None = None,
    visitor_session_id: str | None = None,
) -> dict:
    """Build the changed blocks for an ask plan — whole-block replacements only.

    Returns ``blocks`` (the client merges by id), ``patched_block_ids``, a
    recomputed ``dag``, ``focus_slug`` and ``highlight_slugs``. Pass the tank's
    current ``props.timeSpan`` as ``time_span`` so adding one dated project
    cannot re-depth the whole school.

    An empty ``blocks`` with ``status: ok`` is a valid answer-only turn, not a
    failure. ``status: rejected`` means the patch would have broken the layout
    and nothing should be applied. ``status: error`` means the fetch behind
    the patch itself failed (e.g. the DB is down) — distinct from a
    legitimate empty patch, so the caller should surface it rather than
    silently falling back to answer-only.
    """
    if not isinstance(ask_plan, dict) or not ask_plan:
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["ask_plan"]}
    if not _ask_enabled():
        return {"status": "error", "error": "ask_disabled", "message": "settings.ask.enabled is false"}

    plan = plan_from_dict(ask_plan)
    extra_projects = None
    sid = str(visitor_session_id or "").strip()
    if sid and plan.add_slugs:
        try:
            from plugins.portfolio_plugin.ask.fish_pool import take_from_session

            extra_projects = take_from_session(sid, list(plan.add_slugs)) or None
        except Exception as exc:
            logger.debug("build_ask_overlay: fish pool take fail-open: %s", exc)
            extra_projects = None
    recs = [dict(r) for r in (plan.recommendations or ()) if isinstance(r, dict)]
    pool = [dict(r) for r in recs if not r.get("in_tank")]
    if plan.intent == "discover":
        result = await _spawn_from_discovery(
            question=str(question),
            block_index=block_index or [],
            tank_slugs=tank_slugs or [],
            dag=dag,
            time_span=time_span,
            highlight_slugs=list(plan.highlight_slugs),
            recommendations=recs,
            fish_pool=pool,
            pool_id=plan.pool_id,
        )
    else:
        result = await _build_ask_overlay(
            plan,
            question=str(question),
            tenant_id=current_tenant_id.get(),
            block_index=block_index or [],
            tank_slugs=tank_slugs or [],
            dag=dag,
            time_span=time_span,
            extra_projects=extra_projects,
        )
    if "recommendations" not in result:
        result["recommendations"] = recs
    if "fish_pool" not in result:
        result["fish_pool"] = pool
    if "pool_id" not in result:
        result["pool_id"] = plan.pool_id
    # Mirrored under a board-safe key: the flow envelope owns "status" for the
    # flow's own ok/error outcome, so the overlay's own status (ok/rejected/
    # error) needs a name that doesn't collide when hoisted onto the board.
    result["overlay_status"] = result.get("status")
    if run_id and result.get("status") == "error":
        # Idempotent update of the turn route_portfolio_ask already recorded —
        # never a second row for the same run_id.
        await mark_ask_turn_failed(run_id=run_id, error_type="overlay_build_failed")
    return result


async def _spawn_from_discovery(
    *,
    question: str,
    block_index: list[dict],
    tank_slugs: list[str],
    dag: dict | None,
    time_span: dict | None,
    highlight_slugs: list,
    recommendations: list | None = None,
    fish_pool: list | None = None,
    pool_id: str = "",
) -> dict:
    """Wait on the discovery job and spawn every matched fish (bounded), or degrade to recommendations."""
    recs = [dict(r) for r in (recommendations or []) if isinstance(r, dict)]
    pool = [dict(r) for r in (fish_pool or []) if isinstance(r, dict)]
    job = start_discovery(question, tenant_id=current_tenant_id.get())
    if job.get("status") in {"disabled", "error"}:
        return _empty_overlay(
            dag=dag,
            highlight_slugs=highlight_slugs,
            pending_job=job,
            warnings=[str(job.get("error") or f"discovery {job.get('status')}")],
            recommendations=recs,
            fish_pool=pool,
            pool_id=pool_id,
        )

    result = await await_discovery(str(job.get("job_id") or ""))
    status = str(result.get("status") or "")
    targets = tuple(resolve_targets(block_index, "fishTank"))
    spawned = spawn_projects_from_candidates(result.get("projects") or [])

    # Batch against what's already swimming: a candidate whose slug is
    # already in the tank isn't "missing" and must not eat into the spawn
    # cap or re-trigger a redundant patch for a fish already visible.
    existing = {str(s).strip().lower() for s in (tank_slugs or []) if str(s).strip()}
    already_present = tuple(
        str(s) for p in spawned if (s := str(p.get("slug") or "").strip()) and s.lower() in existing
    )
    missing = [p for p in spawned if str(p.get("slug") or "").strip().lower() not in existing]
    # Every candidate the discovery job actually matched (and isn't already
    # in the tank) spawns, not just the top one — a "show me the AI and
    # DevOps projects" turn should populate the tank with all of them in one
    # round trip.
    slugs = tuple(
        str(s) for p in missing[:MAX_DISCOVERY_SPAWN] if (s := str(p.get("slug") or "").strip())
    )

    if status == "ready" and slugs and targets:
        overlay = await _build_ask_overlay(
            AskPlan(
                intent="add_fish",
                focus_slug=slugs[0],
                add_slugs=slugs,
                target_ids=targets,
                # Already-present matches still deserve a focus pill — the
                # visitor asked about them too, they just don't need a patch.
                highlight_slugs=tuple(dict.fromkeys((*slugs, *already_present))),
                reason=f"spawned {len(slugs)} from discovery",
            ),
            question=question,
            tenant_id=current_tenant_id.get(),
            block_index=block_index,
            tank_slugs=tank_slugs,
            dag=dag,
            time_span=time_span,
            extra_projects=missing,
        )
        # Same-turn spawn: do not hand the client a poll token. A `ready`
        # pending_job made ChatPanel print "looking it up" and re-ask.
        overlay["pending_job"] = None
        return overlay

    if status == "ready" and already_present and not slugs:
        # Every matched candidate is already in the tank — nothing to patch,
        # but the visitor still gets pills to jump to what they asked about.
        return _empty_overlay(
            dag=dag,
            highlight_slugs=list(dict.fromkeys(already_present)),
            pending_job=None,
            warnings=[],
            recommendations=recs,
            fish_pool=pool,
            pool_id=pool_id,
        )

    if not targets:
        cause = "discover: layout has no fishTank block to spawn into"
    elif status == "ready" and not slugs:
        cause = "discover: candidates did not yield a spawnable project"
    else:
        cause = str(result.get("error") or f"discover: job {status or 'unknown'}")
    still_pending = status == "pending"
    return _empty_overlay(
        dag=dag,
        highlight_slugs=highlight_slugs,
        pending_job=result if still_pending else None,
        warnings=[cause],
        recommendations=recs,
        fish_pool=pool,
        pool_id=pool_id,
    )


@mcp.tool(
    title="spawn_pooled_fish",
    tags={"portfolio_plugin", "read", "ask"},
    annotations={"readOnlyHint": True, "idempotentHint": False},
)
async def spawn_pooled_fish(
    pool_id: str,
    slugs: list[str],
    block_index: list[dict] | None = None,
    tank_slugs: list[str] | None = None,
    dag: dict | None = None,
    time_span: dict | None = None,
    visitor_session_id: str | None = None,
) -> dict:
    """Spawn previously-recommended pool fish into the tank on explicit visitor request.

    Redeems the ``pool_id`` a ``recommend``-intent ``build_ask_overlay`` call
    handed back, pulling the requested candidates from the server-side stash
    and running the standard overlay composition + quality gate to patch the
    fishTank block. Distinct from the ``add_slugs`` short-circuit on
    ``route_portfolio_ask`` — that path re-routes through the full router,
    this one goes straight to the tank builder for a fish the visitor already
    saw offered.
    """
    if not pool_id or not str(pool_id).strip() or not slugs or not isinstance(slugs, list):
        missing = []
        if not pool_id or not str(pool_id).strip():
            missing.append("pool_id")
        if not slugs or not isinstance(slugs, list):
            missing.append("slugs")
        return {"status": "error", "error": "missing_required_fields", "missing_fields": missing}
    if not _ask_enabled():
        return {"status": "error", "error": "ask_disabled", "message": "settings.ask.enabled is false"}

    taken = take_from_pool(str(pool_id), list(slugs or [])[:MAX_DISCOVERY_SPAWN])
    taken_slugs = tuple(str(p["slug"]) for p in taken if isinstance(p, dict) and p.get("slug"))
    if not taken or not taken_slugs:
        result = _empty_overlay(
            dag=dag,
            highlight_slugs=[],
            pending_job=None,
            warnings=["spawn_pooled_fish: pool expired or no matching slugs"],
        )
        result["overlay_status"] = result.get("status")
        result["pool_id"] = str(pool_id or "")
        return result

    overlay = await _build_ask_overlay(
        AskPlan(
            intent="add_fish",
            focus_slug=taken_slugs[0],
            add_slugs=taken_slugs,
            target_ids=tuple(resolve_targets(block_index or [], "fishTank")),
            highlight_slugs=taken_slugs,
            reason=f"spawned {len(taken_slugs)} from fish pool",
        ),
        question="",
        tenant_id=current_tenant_id.get(),
        block_index=block_index or [],
        tank_slugs=tank_slugs or [],
        dag=dag,
        time_span=time_span,
        extra_projects=taken,
    )
    overlay["pending_job"] = None
    overlay["overlay_status"] = overlay.get("status")
    # Always echo the redeemed pool_id — the AskPlan built above carries no
    # pool_id of its own (it's not a "recommend" plan), so overlay.py's own
    # pool_id passthrough would otherwise stamp "" over the caller's id.
    overlay["pool_id"] = str(pool_id or "")
    return overlay


@mcp.tool(
    title="start_context_discovery",
    tags={"portfolio_plugin", "read", "ask"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def start_context_discovery(query: str) -> dict:
    """Start a read-only background discovery job for an unmatched question.

    Forced ``dry_run`` / no write-back: a visitor question can never mutate
    ``portfolio_projects``. May index ``portfolio_plugin__context`` when
    ``ask.discovery_index`` is on. Poll with ``get_context_discovery`` or
    wait with ``await_context_discovery``.
    """
    if not query or not str(query).strip():
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["query"]}
    return start_discovery(str(query), tenant_id=current_tenant_id.get())


@mcp.tool(
    title="get_context_discovery",
    tags={"portfolio_plugin", "read", "ask"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_context_discovery(job_id: str) -> dict:
    """Poll a discovery job: ``pending|ready|empty|error|unknown``."""
    if not job_id or not str(job_id).strip():
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["job_id"]}
    return get_discovery(str(job_id))


@mcp.tool(
    title="await_context_discovery",
    tags={"portfolio_plugin", "read", "ask"},
    annotations={"readOnlyHint": True, "idempotentHint": False},
)
async def await_context_discovery(job_id: str, timeout_s: float = 0) -> dict:
    """Wait for a discovery job (default timeout = ``ask.discovery_budget_s``).

    Does not cancel the job on timeout — a late finish still serves the next
    ask of the same query. Operator/manual surface; FlowSpec waits inside
    ``build_ask_overlay`` instead.
    """
    if not job_id or not str(job_id).strip():
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["job_id"]}
    try:
        budget = float(timeout_s or 0)
    except (TypeError, ValueError):
        budget = 0.0
    return await await_discovery(str(job_id), timeout_s=budget)


@mcp.tool(
    title="ensure_ask_answer",
    tags={"portfolio_plugin", "read", "ask"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def ensure_ask_answer(
    question: str = "",
    focus_slug: str = "",
    highlight_slugs: list | None = None,
    answer_markdown: str = "",
    overlay_status: str = "",
    recommendations: list | None = None,
    fish_pool: list | None = None,
) -> dict:
    """Fill ``answer_markdown`` when the agentic stage is empty or a flow debug line.

    Offers fish pool candidates or in-tank recommendations when present.
    ``on_fail: continue`` on the answer stage must still yield visitor prose.
    Does not coerce overlay ``status: error`` into an answer-only success —
    the overlay status is echoed so the client can keep that distinction.
    """
    from plugins.portfolio_plugin.ask.visitor_turn import fallback_answer_markdown

    markdown = fallback_answer_markdown(
        question=str(question or ""),
        focus_slug=str(focus_slug or ""),
        highlight_slugs=list(highlight_slugs or []),
        answer_markdown=str(answer_markdown or ""),
        recommendations=list(recommendations or []),
        pool=list(fish_pool or []),
    )
    return {
        "answer_markdown": markdown,
        "overlay_status": overlay_status,
    }
