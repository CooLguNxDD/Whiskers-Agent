"""Bake agent — persist job layout short_id from draft / job signals."""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.compose.blackboard import PortfolioDraft, get_draft_store
from plugins.portfolio_plugin.compose.recipes import layout_quality_ok

logger = logging.getLogger("whiskers.plugins.portfolio_plugin.agents.bake")


def _parse_job_signals(query: str, job_signals: dict[str, Any] | None) -> dict[str, str]:
    """Extract company / role / description from explicit signals or the query.

    Returns whatever it found, including empties. It deliberately does **not**
    invent a company or role: manufacturing "Whiskers Agent Demo" / "Engineer" only
    produced a bake for a job nobody applied to, and the MCP tool rejects those
    same placeholders anyway. Missing signals are the caller's error to see.
    """
    sig = dict(job_signals or {})
    company = str(sig.get("company") or "").strip()
    role = str(sig.get("role") or "").strip()
    desc = str(sig.get("job_description") or sig.get("description") or query or "").strip()
    # Lightweight parse: "bake portfolio for ROLE at COMPANY"
    import re
    q = (query or "").strip()
    if not company and re.search(r"\s+at\s+", q, re.IGNORECASE):
        # e.g. bake for Senior Engineer at Acme
        parts = re.split(r"(?i)\s+at\s+", q, maxsplit=1)
        if len(parts) == 2:
            # Word-boundary split, not a plain substring split — "for" as a
            # bare substring also matches inside "Platform"/"Performance"/etc,
            # which silently mangled the role (live-observed: "AI Platform
            # Engineer" -> "m Engineer").
            role = role or re.split(r"(?i)\bfor\b", parts[0], maxsplit=1)[-1].strip() or role
            company = parts[1].strip().split(".")[0].strip() or company
    if not desc:
        desc = q
    return {"company": company, "role": role, "job_description": desc}


async def run_bake_agent(
    *,
    draft: PortfolioDraft | None = None,
    draft_id: str | None = None,
    job_description: str = "",
    company: str = "",
    role: str = "",
    theme: str = "",
    use_fragments: bool = True,
    min_blocks: int = 5,
) -> dict[str, Any]:
    """Bake a job layout via bake_portfolio_for_job; fold short_id into draft.

    When the draft already has a quality agentic layout, pass it through so
    bake persists it instead of re-thinning via the fast scoped plan.
    """
    from plugins.portfolio_plugin.MCPTools.bake_tools import bake_portfolio_for_job

    store = get_draft_store()
    d = draft or (store.get(draft_id) if draft_id else None)
    sig = _parse_job_signals(
        (d.query if d else "") or (d.goal if d else "") or job_description,
        {
            **(d.job_signals if d else {}),
            "company": company or None,
            "role": role or None,
            "job_description": job_description or None,
        },
    )
    # Drop Nones from merge
    sig = {k: v for k, v in sig.items() if v}

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

    prebuilt: dict[str, Any] | None = None
    if d and isinstance(d.layout, dict):
        ok, errs = layout_quality_ok(d.layout, goal_class="bake_for_job")
        if ok:
            prebuilt = d.layout
            logger.info("bake agent: persisting draft layout (passthrough)")
        elif len(errs) > 0:
            logger.info(
                "bake agent: draft layout quality weak %s — agentic recompose",
                errs,
            )

    try:
        result = await bake_portfolio_for_job(
            job_description=str(sig.get("job_description") or ""),
            company=str(sig.get("company") or ""),
            role=str(sig.get("role") or ""),
            theme=theme or (d.theme if d else "") or "",
            use_fragments=bool(use_fragments),
            layout=prebuilt,
        )
    except Exception as exc:
        logger.exception("bake agent failed")
        return {"status": "error", "error": "bake_failed", "message": str(exc)[:500]}

    if not isinstance(result, dict):
        return {"status": "error", "error": "bad_bake_result"}

    short_id = result.get("short_id") or result.get("portfolio_job_id")
    layout = result.get("layout")

    if d is not None:
        if isinstance(layout, dict):
            d.layout = layout
            blocks = layout.get("blocks")
            if isinstance(blocks, list):
                d.sections = [b for b in blocks if isinstance(b, dict)]
        if short_id:
            d.short_id = str(short_id)
        d.job_signals = sig
        d.phase = "bake" if result.get("status") == "ok" else d.phase
        d.touch()
        result = dict(result)
        result["draft"] = d.to_public()

    # No second quality bar here. bake_portfolio_for_job now runs the shared
    # contract (bake/contract.py) internally and either hard-fails or reports
    # its verdict, so the agent path and the MCP path are identical by
    # construction rather than by two gates that drifted apart.
    return result
