"""Operator/debug surface for the bake pipeline.

Separated from ``bake_tools`` so the compose/persist path and the read-only
observability tool do not share a module. GOAP-denylisted: never a visitor
plan step.
"""

from __future__ import annotations

import logging

from core.context import mcp

logger = logging.getLogger("whiskers.plugins.portfolio.bake_tools")


@mcp.tool(
    title="list_bake_runs",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def list_bake_runs(
    limit: int = 50,
    degraded_only: bool = False,
) -> dict:
    """Recent ``portfolio_bake_runs`` for the authenticated tenant.

    Operator debug surface for bake observability. Tenant is taken from the
    principal only. GOAP-denylisted — never a visitor plan step.
    """
    from plugins.portfolio_plugin.store import list_bake_runs as fetch_bake_runs
    from plugins.portfolio_plugin.tenant import require_tenant_id

    tid = require_tenant_id()
    if tid is None:
        return {"status": "error", "error": "tenant_unresolved"}
    rows = await fetch_bake_runs(
        tenant_id=tid,
        limit=limit,
        degraded_only=bool(degraded_only),
    )
    return {"status": "ok", "runs": rows, "count": len(rows)}


@mcp.tool(
    title="list_ask_turns",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def list_ask_turns(
    limit: int = 50,
    intent: str | None = None,
) -> dict:
    """Recent ``portfolio_ask_turns`` for the authenticated tenant.

    Operator debug surface for fish-tank ask observability. Tenant is taken
    from the principal only. GOAP-denylisted — never a visitor plan step.
    """
    from plugins.portfolio_plugin.store import list_ask_turns as fetch_ask_turns
    from plugins.portfolio_plugin.tenant import require_tenant_id

    tid = require_tenant_id()
    if tid is None:
        return {"status": "error", "error": "tenant_unresolved"}
    rows = await fetch_ask_turns(
        tenant_id=tid,
        limit=limit,
        intent=intent,
    )
    return {"status": "ok", "turns": rows, "count": len(rows)}
