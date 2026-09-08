"""End-to-end portfolio pipeline (discover → compose → validate → bake).

Lives in portfolio_plugin — the generic MCP specialist stack is
``core_graph.subgraphs.specialist``. Domain runners register via
``register_specialist_domain`` so triage ``specialist_entry`` can delegate here.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.agents.bake import run_bake_agent
from plugins.portfolio_plugin.agents.composer import run_composer_agent
from plugins.portfolio_plugin.agents.discovery import run_discovery_agent
from plugins.portfolio_plugin.agents.validator import validate_with_retries
from plugins.portfolio_plugin.compose.blackboard import get_draft_store
from plugins.portfolio_plugin.compose.recipes import classify_specialist_goal

logger = logging.getLogger("whiskers.plugins.portfolio_plugin.pipeline")


async def run_portfolio_pipeline(
    goal: str,
    *,
    tenant_id: int,
    theme: str = "",
    force_discover: bool = False,
    job_signals: dict[str, Any] | None = None,
    session_id: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run the portfolio discover/compose/bake stack; return response envelope.

    Envelope shape matches graph ``response`` so specialist_entry can stamp it
    onto state and route to summary_node. Extra kwargs are ignored so the
    generic specialist domain signature can pass tool_globs / system_prompt.
    """
    q = (goal or "").strip()
    if not q:
        return {
            "status": "error",
            "error": "missing_goal",
            "message": "specialist pipeline requires a goal/query",
        }

    gclass = classify_specialist_goal(q)
    store = get_draft_store()
    draft = store.create(
        goal=q,
        session_id=session_id,
        query=q,
        theme=theme or None,
        job_signals=dict(job_signals or {}),
        phase="init",
    )
    phases: list[dict[str, Any]] = []

    # Optional discover for inventory refresh.
    # An explicit "discover" goal is a request to refresh the inventory, so it
    # indexes for real. write_back is pinned False rather than left to
    # settings.discovery.write_back (shipped true): gclass comes from a broad
    # substring classifier, so any natural-language goal containing "discover"
    # would otherwise upsert portfolio_projects. Reindexing is recoverable;
    # mutating project rows off an LLM-classified phrase is not. The scheduled
    # worker and the admin-gated discover_portfolio_context tool still write back.
    # A force_discover riding along on some *other* goal stays preview-only:
    # a side-effect path should not write on the user's behalf.
    if gclass == "discover" or force_discover:
        committing = gclass == "discover"
        disc = await run_discovery_agent(
            draft=draft,
            scope="all",
            dry_run=not committing,
            do_index=committing,
            write_back=False,
            tenant_id=tenant_id,
        )
        phases.append({"phase": "discover", "status": disc.get("status")})
        if gclass == "discover":
            # Discover-only goal
            ok = disc.get("status") in ("ok", "partial")
            return _finalize_envelope(
                draft=draft,
                goal_class=gclass,
                phases=phases,
                status="ok" if ok else "error",
                summary=_discover_summary(disc),
                extra={"discovery": disc},
            )

    # Compose + validate (with retries)
    if gclass in ("scoped_ask", "redesign", "bake_for_job"):
        if draft.layout is None:
            comp = await run_composer_agent(
                draft=draft,
                query=q,
                goal_class=gclass if gclass != "bake_for_job" else "redesign",
                theme=theme,
                tenant_id=tenant_id,
            )
            phases.append({"phase": "compose", "status": comp.get("status")})

        val = await validate_with_retries(
            draft=draft,
            goal_class=gclass if gclass != "bake_for_job" else "redesign",
            tenant_id=tenant_id,
            theme=theme,
        )
        phases.append(
            {
                "phase": "validate",
                "status": val.get("status"),
                "attempts": val.get("attempts"),
            }
        )
        if val.get("status") != "ok" and gclass != "bake_for_job":
            # Bake still tries tool-internal compose; redesign/scoped fail closed
            return _finalize_envelope(
                draft=draft,
                goal_class=gclass,
                phases=phases,
                status="error",
                summary="Portfolio layout composition failed validation.",
                extra={
                    "errors": val.get("errors"),
                    "should_retriage": val.get("should_retriage"),
                    "retriage_reason": val.get("retriage_reason"),
                },
            )

    # Bake
    if gclass == "bake_for_job":
        bake = await run_bake_agent(draft=draft, theme=theme)
        phases.append({"phase": "bake", "status": bake.get("status")})
        if bake.get("status") not in ("ok", "partial"):
            return _finalize_envelope(
                draft=draft,
                goal_class=gclass,
                phases=phases,
                status="error",
                summary=bake.get("message") or "Portfolio bake failed.",
                extra={"bake": bake},
            )
        short_id = bake.get("short_id") or draft.short_id
        layout = bake.get("layout") or draft.layout
        n_blocks = (
            len((layout or {}).get("blocks") or [])
            if isinstance(layout, dict)
            else 0
        )
        scoped = 0
        if isinstance(layout, dict):
            meta = layout.get("meta") if isinstance(layout.get("meta"), dict) else {}
            try:
                scoped = int(meta.get("scopedProjectCount") or 0)
            except (TypeError, ValueError):
                scoped = 0
        company = (draft.job_signals or {}).get("company") or ""
        role = (draft.job_signals or {}).get("role") or ""
        job_bits = " ".join(p for p in (company, role) if p).strip()
        job_clause = f" for {job_bits}" if job_bits else ""
        summary = (
            f"Baked job portfolio{job_clause}: short_id={short_id}, "
            f"{n_blocks} block(s), {scoped} project(s) scoped. "
            f"Open with ?j={short_id}."
        )
        if bake.get("quality_errors"):
            summary += f" Quality notes: {bake.get('quality_errors')}."
        return _finalize_envelope(
            draft=draft,
            goal_class=gclass,
            phases=phases,
            status=bake.get("status") or "ok",
            summary=summary,
            extra={
                "short_id": short_id,
                "portfolio_job_id": short_id,
                "query_param": f"j={short_id}" if short_id else None,
                "layout": layout,
                "quality_errors": bake.get("quality_errors"),
            },
        )

    # Redesign / scoped_ask success
    layout = draft.layout
    n_blocks = len((layout or {}).get("blocks") or []) if isinstance(layout, dict) else 0
    return _finalize_envelope(
        draft=draft,
        goal_class=gclass,
        phases=phases,
        status="ok",
        summary=f"Composed portfolio layout ({n_blocks} blocks, mode={gclass}).",
        extra={"layout": layout, "audience": draft.audience, "theme": draft.theme},
    )


def _discover_summary(disc: dict[str, Any]) -> str:
    n = disc.get("doc_count") or 0
    st = disc.get("status")
    if st == "error":
        return f"Discovery failed: {disc.get('error') or disc.get('message')}"
    if st == "partial":
        return f"Discovery partial ({n} docs); some sources missing."
    return f"Discovery complete ({n} docs)."


def _finalize_envelope(
    *,
    draft,
    goal_class: str,
    phases: list,
    status: str,
    summary: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    layout = None
    if extra and isinstance(extra.get("layout"), dict):
        layout = extra["layout"]
    elif draft and isinstance(draft.layout, dict):
        layout = draft.layout

    env: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "message": summary,
        "goal_class": goal_class,
        "phases": phases,
        "draft": draft.to_public() if draft else None,
        "specialist": True,
    }
    if extra:
        for k, v in extra.items():
            if k != "layout" and v is not None:
                env[k] = v
    if layout is not None:
        env["layout"] = layout
        env["carry"] = {"layout": layout}
    draft.phase = "done" if status in ("ok", "partial") else draft.phase
    draft.touch()
    return env
