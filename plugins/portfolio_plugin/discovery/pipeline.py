"""End-to-end discovery pipeline orchestration."""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.discovery.index import CONTEXT_COLLECTION, index_docs, search_context
from plugins.portfolio_plugin.discovery.normalize import normalize_source_results
from plugins.portfolio_plugin.discovery.reconcile import apply_reconcile, plan_reconcile
from plugins.portfolio_plugin.discovery.resolver import (
    _discovery_settings,
    resolve_sources,
)
from plugins.portfolio_plugin.discovery.sources import fetch_all_sources

logger = logging.getLogger("whiskers.plugins.portfolio.discovery")

__all__ = ["CONTEXT_COLLECTION", "run_discovery", "run_rebuild_index"]


async def _token_login() -> str | None:
    try:
        from plugins.portfolio_plugin.MCPTools.context_tools import (
            _github_token_login,
            _resolve_key,
        )

        token = await _resolve_key("GITHUB_TOKEN")
        return await _github_token_login(token)
    except Exception:
        return None


async def run_discovery(
    *,
    scope: str = "all",
    dry_run: bool = True,
    force: bool = False,
    write_back: bool | None = None,
    do_index: bool = True,
    index_on_dry_run: bool = False,
    tenant_id: int = 1,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run discovery → normalize → (optional) index → reconcile plan/apply.

    When ``dry_run`` is True, never writes project rows. The narrow
    ``index_on_dry_run`` opt-in allows ``index_docs`` to write the context vector
    collection while keeping project reconciliation read-only.

    Policy:
    - dry_run=True  → index only when ``do_index`` and ``index_on_dry_run``;
      never write projects; return docs + plan
    - dry_run=False → index if do_index; write projects if write_back
    """
    if settings is None:
        try:
            from plugins.portfolio_plugin.plugin_config import SETTINGS

            settings = SETTINGS if isinstance(SETTINGS, dict) else {}
        except Exception:
            settings = {}

    disc = _discovery_settings(settings)
    effective_write_back = (
        bool(write_back) if write_back is not None else bool(disc.get("write_back"))
    )
    if dry_run:
        effective_write_back = False

    resolved = resolve_sources(settings, scope=scope)
    # Prefer portfolio token login; fall back to proxy-bearer-derived identity.
    login = await _token_login()
    if not login:
        try:
            from plugins.portfolio_plugin.discovery.sources import (
                resolve_github_token_for_discovery,
            )
            from plugins.portfolio_plugin.MCPTools.context_tools import _github_token_login

            tok = await resolve_github_token_for_discovery()
            if tok:
                login = await _github_token_login(tok)
        except Exception:
            logger.debug("pipeline.py: swallowed exception", exc_info=True)

    # Standalone CLI has an empty OperationCatalog (proxies live only in the
    # server process). If nothing resolved, still try local github fallback.
    if not resolved and scope in ("all", "github"):
        from plugins.portfolio_plugin.discovery.resolver import ResolvedSource

        resolved = [
            ResolvedSource(
                capability="github.repos.list",
                plugin_id=None,
                operation_id=None,
                local_tool="list_owned_repos",
                input_schema={},
                args_template={"limit": disc.get("max_items_per_source") or 50},
            )
        ]

    fetched = await fetch_all_sources(
        resolved,
        settings=settings,
        token_login=login,
        budget_s=float(disc.get("budget_s") or 30),
    )
    docs = await normalize_source_results(
        fetched,
        max_items_per_source=int(disc.get("max_items_per_source") or 50),
        max_age_months=int(disc.get("max_repo_age_months") or 24),
        strict_allowlist_repos=bool(disc.get("strict_allowlist_repos", True)),
    )

    # Proxy search often returns a shape/arg set that normalizes to zero docs
    # under strict allowlist (e.g. camelCase perPage, CSV shaping). Fall back to
    # local owned+allowlist fetch so MCP-driven runs still index portfolio repos.
    if (
        scope in ("all", "github")
        and not any(d.source == "github" for d in docs)
    ):
        from plugins.portfolio_plugin.discovery.resolver import ResolvedSource
        from plugins.portfolio_plugin.discovery.sources import fetch_resolved

        local = ResolvedSource(
            capability="github.repos.list",
            plugin_id=None,
            operation_id=None,
            local_tool="list_owned_repos",
            input_schema={},
            args_template={
                "limit": int(disc.get("max_items_per_source") or 50),
                "per_page": 100,
            },
        )
        try:
            local_env = await fetch_resolved(local, settings=settings, token_login=login)
            fetched.append(local_env)
            docs = await normalize_source_results(
                fetched,
                max_items_per_source=int(disc.get("max_items_per_source") or 50),
                max_age_months=int(disc.get("max_repo_age_months") or 24),
                strict_allowlist_repos=bool(disc.get("strict_allowlist_repos", True)),
            )
            if docs:
                logger.info(
                    "discovery: local allowlist fallback produced %d doc(s) after empty proxy normalize",
                    len(docs),
                )
        except Exception as exc:
            logger.warning("discovery local fallback failed open: %s", exc)

    try:
        from plugins.portfolio_plugin.store import list_projects

        existing = await list_projects(include_inactive=True, tenant_id=tenant_id)
    except Exception as exc:
        logger.warning("list_projects failed open: %s", exc)
        existing = []

    plan = plan_reconcile(docs, existing)

    index_result: dict[str, Any] | None = None
    if do_index and (not dry_run or index_on_dry_run):
        index_result = await index_docs(docs, tenant_id=tenant_id, force=force)
    elif dry_run and do_index:
        index_result = {"status": "skipped", "reason": "dry_run"}

    reconcile_result = await apply_reconcile(
        plan, tenant_id=tenant_id, write_back=effective_write_back and not dry_run
    )

    hints: list[str] = []
    proxy_resolved = any(r.plugin_id for r in resolved)
    if not proxy_resolved:
        hints.append(
            "No live proxy ops bound (OperationCatalog empty in this process is normal "
            "outside the MCP server). Local GitHub fallbacks only. "
            "Prefer specialist discovery agent / discover_portfolio_context via the running MCP server."
        )
    for f in fetched:
        if f.get("status") == "not_configured":
            hints.append(
                f"{f.get('capability')}: not_configured — set portfolio_plugin GITHUB_TOKEN "
                "or rely on proxy_github-* bearer / github_allowlist.repos public fetch."
            )
        if f.get("status") == "error":
            hints.append(f"{f.get('capability')}: error — check fetched.raw message")
    if not docs:
        hints.append(
            "Zero docs after normalize. Common causes: missing token, empty allowlist repos, "
            "all repos filtered (fork/archived/age), or owner outside github_allowlist."
        )

    return {
        "status": "ok",
        "dry_run": dry_run,
        "write_back": effective_write_back and not dry_run,
        "scope": scope,
        "collection": CONTEXT_COLLECTION,
        "token_login": login,
        "resolved": [
            {
                "capability": r.capability,
                "plugin_id": r.plugin_id,
                "operation_id": r.operation_id,
                "local_tool": r.local_tool,
            }
            for r in resolved
        ],
        "fetched": [
            {
                "capability": f.get("capability"),
                "via": f.get("via"),
                "status": f.get("status"),
                "local_tool": f.get("local_tool"),
                "plugin_id": f.get("plugin_id"),
                "message": (
                    (f.get("raw") or {}).get("message")
                    if isinstance(f.get("raw"), dict)
                    else None
                ),
                "repo_count": (
                    (f.get("raw") or {}).get("count")
                    if isinstance(f.get("raw"), dict)
                    else None
                ),
                "via_detail": (
                    (f.get("raw") or {}).get("via")
                    if isinstance(f.get("raw"), dict)
                    else None
                ),
            }
            for f in fetched
        ],
        "docs": [d.to_dict() for d in docs],
        "doc_count": len(docs),
        "reconcile_plan": plan,
        "index": index_result,
        "reconcile": reconcile_result,
        "hints": hints,
    }


async def run_rebuild_index(
    *,
    force: bool = False,
    tenant_id: int = 1,
    scope: str = "all",
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Discover + index only (no project write-back)."""
    result = await run_discovery(
        scope=scope,
        dry_run=False,
        force=force,
        write_back=False,
        do_index=True,
        tenant_id=tenant_id,
        settings=settings,
    )
    return {
        "status": result.get("status"),
        "collection": CONTEXT_COLLECTION,
        "doc_count": result.get("doc_count"),
        "index": result.get("index"),
        "resolved": result.get("resolved"),
        "fetched": result.get("fetched"),
    }


async def index_is_empty_or_stale(
    *,
    tenant_id: int = 1,
    freshness_s: float = 21600,
) -> bool:
    """True when the context collection has no usable fresh hits."""
    hits = await search_context("portfolio project", tenant_id=tenant_id, top_k=3)
    if not hits:
        return True
    from plugins.portfolio_plugin.discovery.index import is_fresh

    return not is_fresh(hits, freshness_s)
