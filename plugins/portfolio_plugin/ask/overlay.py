"""Build the changed blocks for one ask turn — the overlay itself is stateless.

Returns **only** the blocks that changed plus a recomputed DAG, so the client
merges by id and the wire never carries a whole layout. No DB write, no
``short_id``: an ask overlay lives in the visitor's session and dies on reload.

The *overlay content* stays ephemeral, but the *turn* (question, intent,
outcome) is durably audited one level up, in
``MCPTools/ask_tools.py::route_portfolio_ask`` via ``ask/telemetry.py`` — see
that module for the ``portfolio_ask_turns`` table this function's callers
write to. Nothing about the layout itself is ever persisted from here.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.ask.contract import assess_patch_quality
from plugins.portfolio_plugin.ask.discovery_jobs import _tenant_int
from plugins.portfolio_plugin.ask.router import AskPlan
from plugins.portfolio_plugin.ask.targets import tank_entry


def _recommendations_of(plan: AskPlan) -> list[dict[str, Any]]:
    """JSON-safe copy of the plan's recommendation chips; empty when none."""
    return [dict(r) for r in (plan.recommendations or ()) if isinstance(r, dict) and r.get("slug")]


def _fish_pool_of(plan: AskPlan) -> list[dict[str, Any]]:
    """JSON-safe copy of the not-in-tank subset stashed under ``plan.pool_id``."""
    return [
        dict(r)
        for r in (plan.recommendations or ())
        if isinstance(r, dict) and r.get("slug") and not r.get("in_tank")
    ]

logger = logging.getLogger("whiskers.plugins.portfolio.ask.overlay")


def _strip_private(block: dict[str, Any]) -> dict[str, Any]:
    """Drop compose-private keys that must not reach a client."""
    out = dict(block)
    out.pop("_sourceRefs", None)
    return out


async def _build_tank_block(
    plan: AskPlan,
    *,
    question: str,
    tenant_id: int,
    block_index: list[dict[str, Any]],
    tank_slugs: list[str],
    time_span: dict[str, Any] | None,
    extra_projects: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, list[str], bool]:
    """Rebuild the fishTank block, scoped to the tank's own roster + any adds.

    Third return value is ``fatal`` — true only when the inventory fetch
    itself failed (a DB error), never for a plan/roster mismatch. Callers use
    it to tell "nothing to patch" (still ``ok``) from "we don't actually know"
    (should surface as an error, not a silent answer-only turn).
    """
    from plugins.portfolio_plugin.compose.fish import build_fish_tank_block
    from plugins.portfolio_plugin.compose.job_tailor import job_tokens
    from plugins.portfolio_plugin.compose.quality import is_portfolio_worthy_project
    from plugins.portfolio_plugin.store import list_projects

    entry = tank_entry(block_index)
    if entry is None:
        return None, ["fishTank: layout has no tank block to patch"], False

    try:
        rows = await list_projects(tenant_id=_tenant_int(tenant_id)) or []
    except Exception as exc:
        logger.warning("ask overlay: list_projects failed: %s", exc)
        return None, [f"fishTank: inventory unavailable ({exc})"[:200]], True

    # Roster = whatever is in the tank today plus the grounded adds. Never the
    # whole inventory — a focus turn must not silently repopulate the tank.
    roster = {str(s).strip().lower() for s in (tank_slugs or []) if str(s).strip()}
    roster.update(str(s).strip().lower() for s in plan.add_slugs if s)
    if not roster:
        return None, ["fishTank: empty roster"], False

    scoped = [
        p
        for p in rows
        if isinstance(p, dict) and str(p.get("slug") or "").strip().lower() in roster
    ]
    seen = {str(p.get("slug") or "").strip().lower(): i for i, p in enumerate(scoped)}
    # Virtual / discovered projects (not in portfolio_projects) join the
    # roster here. They are not "missing" — there is no DB row to warn about.
    # A slug already in `scoped` from a real-but-thin DB row must not shadow
    # a spawned candidate for the same slug: spawn.py guarantees a worthy
    # summary/link, a stale placeholder row does not, and the worthy gate in
    # build_fish_tank_block would otherwise drop the real row and the extra
    # never gets a turn — net result, the fish silently vanishes.
    for extra in extra_projects or []:
        if not isinstance(extra, dict):
            continue
        slug = str(extra.get("slug") or "").strip().lower()
        if not slug or slug not in roster:
            continue
        if slug not in seen:
            seen[slug] = len(scoped)
            scoped.append(extra)
        elif not is_portfolio_worthy_project(scoped[seen[slug]]):
            scoped[seen[slug]] = extra
    missing = roster - set(seen)
    errors = [f"fishTank: no active project row for '{s}'" for s in sorted(missing)]
    if not scoped:
        return None, errors or ["fishTank: roster resolved to no projects"], False

    block = build_fish_tank_block(
        scoped,
        highlight_slugs=list(plan.highlight_slugs),
        block_id=str(entry.get("id") or "fish-tank-1"),
        job_tokens=job_tokens("", "", question),
        # Frozen span: adding one dated project must not re-depth the school.
        time_span=time_span,
    )
    if not isinstance(block, dict):
        return None, errors or ["fishTank: builder returned no block"], False
    return block, errors, False


async def _build_text_blocks(
    plan: AskPlan,
    *,
    question: str,
    tenant_id: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run the plan's block steps through the shared single-block builder."""
    from plugins.portfolio_plugin.compose.block_builder import build_layout_block_impl

    blocks: list[dict[str, Any]] = []
    errors: list[str] = []
    for step in plan.block_steps:
        btype = str(step.get("block_type") or "").strip()
        if not btype:
            continue
        try:
            res = await build_layout_block_impl(
                btype,
                tenant_id=_tenant_int(tenant_id),
                query=str(step.get("query") or question),
                slugs=list(step.get("slugs") or []) or None,
                top_k=int(step.get("top_k") or 4),
                block_id=str(step.get("block_id") or ""),
                kind="db",
            )
        except Exception as exc:
            logger.warning("ask overlay: build %s failed: %s", btype, exc)
            errors.append(f"{btype}: {exc}"[:300])
            continue
        if not isinstance(res, dict) or res.get("status") != "ok":
            errors.append(f"{btype}: {(res or {}).get('errors')}"[:300])
            continue
        # ``card`` expands into one block per resolved project.
        multi = res.get("blocks") if isinstance(res.get("blocks"), list) else None
        for block in multi or [res.get("block")]:
            if isinstance(block, dict):
                blocks.append(_strip_private(block))
    return blocks, errors


async def build_ask_overlay(
    plan: AskPlan,
    *,
    question: str,
    tenant_id: int = 1,
    block_index: list[dict[str, Any]] | None = None,
    tank_slugs: list[str] | None = None,
    dag: dict[str, Any] | None = None,
    time_span: dict[str, Any] | None = None,
    extra_projects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the changed blocks for *plan*.

    Returns ``{status, blocks, patched_block_ids, dag, focus_slug,
    highlight_slugs, warnings, errors, recommendations, fish_pool,
    pool_id}``. ``blocks`` is the whole-block replacement set — the client
    merges by id. ``fish_pool`` is the not-in-tank subset of
    ``recommendations``, stashed server-side under ``pool_id`` so a later
    ``spawn_pooled_fish`` call can redeem it without re-ranking.
    ``status: "error"`` means the fetch that would have produced the patch
    itself failed (e.g. the DB is down) — distinct from ``status: "ok"`` with
    empty ``blocks``, which is a legitimate answer-only turn.
    """
    from plugins.portfolio_plugin.compose.patch import merge_dag_bands

    index = [e for e in (block_index or []) if isinstance(e, dict)]
    errors: list[str] = []
    blocks: list[dict[str, Any]] = []
    fatal = False

    in_tank = {str(s).strip().lower() for s in (tank_slugs or []) if str(s).strip()}
    spawn_slugs = [s for s in plan.add_slugs if s]
    focus = str(plan.focus_slug or "").strip().lower()
    if focus and focus not in in_tank and focus not in spawn_slugs:
        spawn_slugs.append(focus)
    need_tank = plan.intent in {"focus_fish", "add_fish"} or bool(spawn_slugs)

    if plan.intent == "recommend":
        return {
            "status": "ok",
            "blocks": [],
            "patched_block_ids": [],
            "dag": dag if isinstance(dag, dict) else None,
            "focus_slug": plan.focus_slug,
            "highlight_slugs": list(plan.highlight_slugs),
            "recommendations": _recommendations_of(plan),
            "fish_pool": _fish_pool_of(plan),
            "pool_id": plan.pool_id,
            "warnings": [],
            "errors": [],
        }

    if need_tank:
        tank_plan = plan
        if plan.intent == "patch_blocks" and spawn_slugs:
            tank_plan = AskPlan(
                intent="add_fish",
                focus_slug=plan.focus_slug,
                add_slugs=tuple(spawn_slugs),
                target_ids=plan.target_ids,
                highlight_slugs=plan.highlight_slugs,
                ranked_slugs=plan.ranked_slugs,
                pool_slugs=plan.pool_slugs,
                recommend_slugs=plan.recommend_slugs,
                recommendations=plan.recommendations,
                reason=plan.reason,
            )
        block, errs, block_fatal = await _build_tank_block(
            tank_plan,
            question=question,
            tenant_id=tenant_id,
            block_index=index,
            tank_slugs=list(tank_slugs or []),
            time_span=time_span,
            extra_projects=extra_projects,
        )
        errors.extend(errs)
        fatal = fatal or block_fatal
        if isinstance(block, dict):
            blocks.append(block)
    if plan.intent == "patch_blocks":
        built, errs = await _build_text_blocks(plan, question=question, tenant_id=tenant_id)
        errors.extend(errs)
        blocks.extend(built)

    if not blocks:
        recs = _recommendations_of(plan)
        if fatal:
            # A real fetch failure, not a legitimate empty patch — surface it
            # rather than silently degrading to an answer-only "ok" turn.
            return {
                "status": "error",
                "blocks": [],
                "patched_block_ids": [],
                "dag": dag if isinstance(dag, dict) else None,
                "focus_slug": plan.focus_slug,
                "highlight_slugs": list(plan.highlight_slugs),
                "recommendations": recs,
                "fish_pool": _fish_pool_of(plan),
                "pool_id": plan.pool_id,
                "warnings": [],
                "errors": errors,
            }
        # Recommend is answer-shaped (no blocks) but carries chips. Answer-only
        # is a valid outcome, not a failure: focus still works client-side.
        return {
            "status": "ok",
            "blocks": [],
            "patched_block_ids": [],
            "dag": dag if isinstance(dag, dict) else None,
            "focus_slug": plan.focus_slug,
            "highlight_slugs": list(plan.highlight_slugs),
            "recommendations": recs,
            "fish_pool": _fish_pool_of(plan),
            "pool_id": plan.pool_id,
            "warnings": errors,
            "errors": [],
        }

    requested = [str(t) for t in plan.target_ids if t]
    for bid in (str(b.get("id")) for b in blocks if b.get("id")):
        if bid not in requested:
            requested.append(bid)
    verdict = assess_patch_quality(
        blocks,
        requested_ids=requested,
        base_index=index,
    )
    if not verdict["ok"]:
        logger.warning("ask overlay: patch rejected: %s", verdict["errors"])
        return {
            "status": "rejected",
            "blocks": [],
            "patched_block_ids": [],
            "dag": dag if isinstance(dag, dict) else None,
            "focus_slug": plan.focus_slug,
            "highlight_slugs": list(plan.highlight_slugs),
            "recommendations": _recommendations_of(plan),
            "fish_pool": _fish_pool_of(plan),
            "pool_id": plan.pool_id,
            "warnings": errors,
            "errors": verdict["errors"],
        }

    patched_ids = [str(b.get("id")) for b in blocks if b.get("id")]
    # Bands from the index + new ids only — merge_dag_bands needs (id, type)
    # pairs, never full props, which is why the client ships a skeleton.
    band_input = [{"id": e.get("id"), "type": e.get("type")} for e in index]
    known = {str(e.get("id")) for e in index}
    band_input.extend(
        {"id": b.get("id"), "type": b.get("type")}
        for b in blocks
        if str(b.get("id")) not in known
    )
    merged_dag = merge_dag_bands(dag if isinstance(dag, dict) else None, band_input)

    return {
        "status": "ok",
        "blocks": blocks,
        "patched_block_ids": patched_ids,
        "dag": merged_dag,
        "focus_slug": plan.focus_slug,
        "highlight_slugs": list(plan.highlight_slugs),
        "recommendations": _recommendations_of(plan),
        "fish_pool": _fish_pool_of(plan),
        "pool_id": plan.pool_id,
        "warnings": errors + list(verdict["warnings"]),
        "errors": [],
    }
