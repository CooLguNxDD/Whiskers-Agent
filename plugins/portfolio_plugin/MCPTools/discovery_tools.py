"""MCP tools for portfolio context discovery and index rebuild.

GOAP-denylisted — operator/CLI surfaces only, never visitor plan steps.
"""

from __future__ import annotations

import logging

from core.context import current_tenant_id, mcp

logger = logging.getLogger("whiskers.plugins.portfolio.discovery")


def _tenant() -> int:
    try:
        tid = current_tenant_id.get()
        return int(tid) if tid is not None else 1
    except Exception:
        return 1


def _caller_is_admin() -> bool:
    """True when the current request principal is admin/master or has admin scope.

    Used to force discovery ``write_back`` of project rows for operator runs
    even when ``settings.discovery.write_back`` defaults to false.
    """
    try:
        from core.scope_management import get_request_principal, role_has_admin_bypass
        from core.scope_management.sentinels import SCOPE_ADMIN, SCOPE_ALL, SCOPE_WILDCARD

        grant = get_request_principal()
        if grant is None:
            return False
        if role_has_admin_bypass(grant.role):
            return True
        role = (grant.role or "").strip().lower()
        if role in ("admin", "master"):
            return True
        scopes = list(grant.scopes or [])
        return any(
            s in (SCOPE_ADMIN, SCOPE_ALL, SCOPE_WILDCARD, "admin", "all", "*")
            for s in scopes
        )
    except Exception:
        return False


@mcp.tool(
    title="discover_portfolio_context",
    tags={"portfolio_plugin", "admin"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def discover_portfolio_context(
    scope: str = "all",
    dry_run: bool = True,
    force: bool = False,
    write_back: bool | None = None,
) -> dict:
    """Discover GitHub/Notion portfolio context via live proxies or local fallbacks.

    Binds manifest discovery intents to OperationCatalog proxy ops (or local
    ``list_owned_repos``), normalizes into ContextDocs, and optionally indexes
    / reconciles project rows.

    Notion inventory comes only from mounted Notion MCP proxies (e.g.
    ``proxy_Notion-AndrewDev`` / ``notion-search``) — not REST API keys.

    Args:
        scope: ``all`` | ``github`` | ``notion``.
        dry_run: When True (default), return docs + reconcile plan with zero writes.
        force: Reserved for forced re-index when not dry_run.
        write_back: When True and dry_run=False, upsert portfolio_projects from
            the reconcile plan. When omitted: **admin** callers default to True
            on commit (dry_run=False); otherwise uses
            ``settings.discovery.write_back`` (default false).

    Returns status envelope with resolved sources, docs, reconcile_plan, index.
    """
    from plugins.portfolio_plugin.agents.discovery import run_discovery_agent

    scope_n = (scope or "all").strip().lower()
    if scope_n not in ("all", "github", "notion"):
        return {
            "status": "error",
            "error": "invalid_scope",
            "message": "scope must be all|github|notion",
        }

    # Admin commit path: promote reconcile plan → project rows unless explicitly off
    effective_wb = write_back
    if effective_wb is None and not dry_run and _caller_is_admin():
        effective_wb = True
        logger.info(
            "discover_portfolio_context: admin principal — write_back=True (dry_run=False)"
        )

    try:
        return await run_discovery_agent(
            scope=scope_n,
            dry_run=bool(dry_run),
            force=bool(force),
            write_back=effective_wb,
            tenant_id=_tenant(),
        )
    except Exception as exc:
        logger.exception("discover_portfolio_context failed")
        return {"status": "error", "error": "discovery_failed", "message": str(exc)[:500]}


@mcp.tool(
    title="rebuild_portfolio_index",
    tags={"portfolio_plugin", "admin"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def rebuild_portfolio_index(force: bool = False, scope: str = "all") -> dict:
    """Re-run discovery and write only to portfolio_plugin__context (no project write-back).

    Args:
        force: Reserved force flag for future re-embed semantics.
        scope: ``all`` | ``github`` | ``notion``.
    """
    from plugins.portfolio_plugin.discovery.pipeline import run_rebuild_index

    scope_n = (scope or "all").strip().lower()
    if scope_n not in ("all", "github", "notion"):
        return {
            "status": "error",
            "error": "invalid_scope",
            "message": "scope must be all|github|notion",
        }

    try:
        return await run_rebuild_index(
            force=bool(force),
            scope=scope_n,
            tenant_id=_tenant(),
        )
    except Exception as exc:
        logger.exception("rebuild_portfolio_index failed")
        return {"status": "error", "error": "rebuild_failed", "message": str(exc)[:500]}
