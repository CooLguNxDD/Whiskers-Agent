"""Capability resolver — bind manifest discovery intents to live catalog ops.

Scans OperationCatalog for proxy operations matching prefer globs +
operation_id substrings. Falls back to local portfolio tools. Never fails the
run: unmatched capabilities are skipped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.discovery")

# Built-in defaults when manifest omits settings.discovery.sources
DEFAULT_SOURCES: list[dict[str, Any]] = [
    {
        "capability": "github.repos.list",
        "prefer": ["proxy_github-*", "proxy_github*"],
        "match": {
            "operation_contains": [
                "search_repositories",
                "list_repos",
                "list_user_repos",
                "repos_list",
                "user_repos",
            ]
        },
        "args": {"query": "user:{token_login} sort:updated", "per_page": 100, "limit": 100},
        "fallback_tool": "list_owned_repos",
    },
    {
        "capability": "notion.pages.search",
        "prefer": ["proxy_Notion-*", "proxy_notion-*", "proxy_Notion*", "proxy_notion*"],
        "match": {
            "operation_contains": [
                "notion-search",
                "notion_search",
                "API-post-search",
            ]
        },
        "args": {
            "query": "{discovery_query}",
            "page_size": 25,
            "max_highlight_length": 400,
        },
        "fallback_tool": None,
    },
]


@dataclass(frozen=True)
class ResolvedSource:
    """One discovery capability bound to a proxy op or local tool."""

    capability: str
    plugin_id: str | None  # None => local tool fallback
    operation_id: str | None
    local_tool: str | None
    input_schema: dict
    args_template: dict
    prefer: tuple[str, ...] = ()
    match_contains: tuple[str, ...] = ()


def _discovery_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return discovery settings dict with defaults filled in."""
    if settings is None:
        try:
            from plugins.portfolio_plugin.plugin_config import SETTINGS

            settings = SETTINGS if isinstance(SETTINGS, dict) else {}
        except Exception:
            settings = {}
    disc = settings.get("discovery") if isinstance(settings.get("discovery"), dict) else {}
    out = {
        "enabled": bool(disc.get("enabled", True)),
        "write_back": bool(disc.get("write_back", False)),
        # Bulk discovery only promotes explicit github_allowlist.repos (+ hero
        # owner/repo links). Owner-wide / token-login widen remains for
        # fetch_external_context, not for inventoring every owned repo.
        "strict_allowlist_repos": bool(disc.get("strict_allowlist_repos", True)),
        "freshness_s": int(disc.get("freshness_s") or 21600),
        "max_items_per_source": int(disc.get("max_items_per_source") or 50),
        "budget_s": float(disc.get("budget_s") or 30),
        "discovery_query": str(
            disc.get("discovery_query") or "project OR portfolio OR case study"
        ),
        "max_repo_age_months": int(disc.get("max_repo_age_months") or 24),
        "sources": disc.get("sources") if isinstance(disc.get("sources"), list) else None,
    }
    if not out["sources"]:
        out["sources"] = list(DEFAULT_SOURCES)
    return out


# Canonical proxy-detection / prefer-glob helpers live in core_graph.agent_loop.toolset
# (shared with the generic agent tool-calling loop) — re-exported here for back-compat
# so existing call sites (``_is_proxy_op`` / ``_prefer_match``) keep working.
from core_graph.agent_loop.toolset import is_proxy_op as _is_proxy_op  # noqa: E402
from core_graph.agent_loop.toolset import prefer_match as _prefer_match  # noqa: E402
from core_graph.agent_loop.toolset import tags_of as _tags_of  # noqa: E402


def _op_match_score(operation_id: str, needles: list[str]) -> int:
    """Higher score for stronger operation_id matches (exact > prefix > contains)."""
    if not needles:
        return 1
    op_l = (operation_id or "").lower()
    best = 0
    for n in needles:
        n_l = (n or "").lower()
        if not n_l:
            continue
        if op_l == n_l:
            best = max(best, 100)
        elif op_l.endswith(n_l) or op_l.startswith(n_l):
            best = max(best, 80)
        elif n_l in op_l:
            best = max(best, 50)
        # strip common proxy prefixes for a second pass
        stripped = op_l
        for prefix in ("proxy_",):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :]
        # also strip first segment before underscore-ish glue
        if "__" in stripped:
            stripped = stripped.split("__", 1)[-1]
        if n_l in stripped:
            best = max(best, 40)
    return best


def resolve_sources(
    settings: dict[str, Any] | None = None,
    *,
    scope: str = "all",
    catalog: Any | None = None,
) -> list[ResolvedSource]:
    """Bind discovery intents to live catalog ops or local fallbacks.

    ``scope`` filters capabilities: ``all`` | ``github`` | ``notion``.
    Never raises — unmatched sources are omitted.
    """
    disc = _discovery_settings(settings)
    scope_l = (scope or "all").lower().strip()
    raw_sources = disc["sources"] or []

    ops: list[Any] = []
    try:
        if catalog is None:
            from core.route_registry.operation_catalog import get_operation_catalog

            catalog = get_operation_catalog()
        ops = list(catalog.all()) if catalog is not None else []
    except Exception as exc:
        logger.debug("discovery resolver: catalog unavailable: %s", exc)
        ops = []

    resolved: list[ResolvedSource] = []
    for src in raw_sources:
        if not isinstance(src, dict):
            continue
        capability = str(src.get("capability") or "").strip()
        if not capability:
            continue
        if scope_l == "github" and "github" not in capability.lower():
            continue
        if scope_l == "notion" and "notion" not in capability.lower():
            continue

        prefer = [str(p) for p in (src.get("prefer") or []) if p]
        match_cfg = src.get("match") if isinstance(src.get("match"), dict) else {}
        needles = [
            str(n)
            for n in (match_cfg.get("operation_contains") or [])
            if n
        ]
        args_template = dict(src.get("args") or {}) if isinstance(src.get("args"), dict) else {}
        fallback = src.get("fallback_tool")
        fallback_tool = str(fallback).strip() if fallback else None

        best: tuple[int, Any] | None = None
        for op in ops:
            if not _is_proxy_op(op):
                continue
            pid = str(getattr(op, "plugin_id", "") or "")
            oid = str(getattr(op, "operation_id", "") or "")
            if not _prefer_match(pid, prefer):
                continue
            score = _op_match_score(oid, needles)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, op)

        if best is not None:
            op = best[1]
            schema = getattr(op, "input_schema", None) or {}
            if not isinstance(schema, dict):
                schema = {}
            resolved.append(
                ResolvedSource(
                    capability=capability,
                    plugin_id=str(op.plugin_id),
                    operation_id=str(op.operation_id),
                    local_tool=None,
                    input_schema=schema,
                    args_template=args_template,
                    prefer=tuple(prefer),
                    match_contains=tuple(needles),
                )
            )
            logger.info(
                "discovery resolve %s → %s/%s (score=%d)",
                capability,
                op.plugin_id,
                op.operation_id,
                best[0],
            )
            continue

        if fallback_tool:
            resolved.append(
                ResolvedSource(
                    capability=capability,
                    plugin_id=None,
                    operation_id=None,
                    local_tool=fallback_tool,
                    input_schema={},
                    args_template=args_template,
                    prefer=tuple(prefer),
                    match_contains=tuple(needles),
                )
            )
            logger.info("discovery resolve %s → local %s", capability, fallback_tool)
        else:
            logger.info("discovery resolve %s → skip (no proxy match, no fallback)", capability)

    return resolved
