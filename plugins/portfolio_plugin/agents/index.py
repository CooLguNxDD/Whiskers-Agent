"""Index agent — write discovery docs into portfolio_plugin__context."""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.compose.blackboard import PortfolioDraft, get_draft_store

logger = logging.getLogger("whiskers.plugins.portfolio_plugin.agents.index")


async def run_index_agent(
    *,
    draft: PortfolioDraft | None = None,
    draft_id: str | None = None,
    force: bool = False,
    scope: str = "all",
    tenant_id: int | None = None,
) -> dict[str, Any]:
    """Re-run discovery with index writes (no project write-back by default)."""
    from core.context import current_tenant_id
    from plugins.portfolio_plugin.discovery.pipeline import run_rebuild_index

    tid = tenant_id
    if tid is None:
        try:
            raw = current_tenant_id.get()
            tid = int(raw) if raw is not None else 1
        except Exception:
            tid = 1

    scope_n = (scope or "all").strip().lower()
    if scope_n not in ("all", "github", "notion"):
        return {
            "status": "error",
            "error": "invalid_scope",
            "message": "scope must be all|github|notion",
        }

    try:
        result = await run_rebuild_index(
            force=bool(force),
            scope=scope_n,
            tenant_id=int(tid),
        )
    except Exception as exc:
        logger.exception("run_index_agent failed")
        return {"status": "error", "error": "index_failed", "message": str(exc)[:500]}

    store = get_draft_store()
    d = draft
    if d is None and draft_id:
        d = store.get(draft_id)
    if d is not None:
        d.index_report = result.get("index") if isinstance(result, dict) else result
        if result.get("docs"):
            d.context_docs = list(result.get("docs") or d.context_docs)
        d.phase = "index"
        d.touch()
        result["draft"] = d.to_public()

    return result
