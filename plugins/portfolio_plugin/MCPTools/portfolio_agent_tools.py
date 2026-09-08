"""MCP surface for the portfolio specialist stack (PortfolioAgent_*)."""

from __future__ import annotations

import logging

from core.context import current_tenant_id, mcp

logger = logging.getLogger("whiskers.plugins.portfolio_plugin.mcp")


def _ensure_specialist_domain() -> None:
    """Idempotent domain register so specialist_entry works once MCP tools load."""
    try:
        from core_graph.subgraphs.specialist.registry import (
            list_specialist_domains,
            register_specialist_domain,
        )
        from plugins.portfolio_plugin.pipeline import run_portfolio_pipeline

        if "portfolio" in list_specialist_domains():
            return

        # Same claim rules as PortfolioPlugin._register_specialist_domain
        from plugins.portfolio_plugin.plugin_config import PortfolioPlugin

        PortfolioPlugin()._register_specialist_domain()
    except Exception as exc:
        logger.debug("portfolio specialist domain ensure skipped: %s", exc)


_ensure_specialist_domain()


@mcp.tool(
    title="PortfolioAgent_run",
    tags={"PortfolioAgent", "portfolio_plugin", "specialist"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def PortfolioAgent_run(
    goal: str,
    theme: str = "",
    force_discover: bool = False,
    company: str = "",
    role: str = "",
    job_description: str = "",
) -> dict:
    """Run the portfolio pipeline (discover / compose / bake).

    Prefer this single tool for multi-block redesign or job bake instead of
    hand-chaining design_layout steps under classic GOAP.

    Args:
        goal: Natural-language portfolio goal (redesign, bake, discover, scoped ask).
        theme: Optional layout theme (cozy|neon|paper|latte|frappe|macchiato|mocha).
        force_discover: When true, run discovery dry-run before compose.
        company: Optional bake signal (employer).
        role: Optional bake signal (role title).
        job_description: Optional bake signal (posting body).
    """
    from plugins.portfolio_plugin.pipeline import run_portfolio_pipeline

    if not goal or not str(goal).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["goal"],
        }

    try:
        raw = current_tenant_id.get()
        tid = int(raw) if raw is not None else 1
    except Exception:
        tid = 1
    if tid <= 0:
        tid = 1

    job_signals = {}
    if company:
        job_signals["company"] = company
    if role:
        job_signals["role"] = role
    if job_description:
        job_signals["job_description"] = job_description

    try:
        return await run_portfolio_pipeline(
            str(goal).strip(),
            tenant_id=tid,
            theme=theme or "",
            force_discover=bool(force_discover),
            job_signals=job_signals or None,
        )
    except Exception as exc:
        logger.exception("PortfolioAgent_run failed")
        return {"status": "error", "error": "pipeline_failed", "message": str(exc)[:500]}
