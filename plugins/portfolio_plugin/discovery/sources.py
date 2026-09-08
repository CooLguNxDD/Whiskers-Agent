"""Execute resolved discovery sources (proxy-first, local fallback).

Proxy rules (load-bearing):
- Callables never raise — check ``status`` on dict results.
- Never prune kwargs against ``inspect.signature`` for proxy callables.
- Pass ``_response_shape={"response_format": "json"}`` so indexer gets JSON not CSV.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from typing import Any

from plugins.portfolio_plugin.discovery.resolver import ResolvedSource, _discovery_settings

logger = logging.getLogger("whiskers.plugins.portfolio.discovery")

_RESPONSE_SHAPE = {"response_format": "json"}


def _unwrap_envelope(result: Any) -> Any:
    """Flatten status-less {_meta, data} discovery envelopes."""
    try:
        from core_graph.node.execute_step import _unwrap_discovery_envelope

        return _unwrap_discovery_envelope(result)
    except Exception:
        if (
            isinstance(result, dict)
            and "status" not in result
            and "_meta" in result
            and "data" in result
        ):
            return result["data"]
        return result


def _fill_args_template(
    template: dict[str, Any],
    *,
    discovery_query: str,
    token_login: str | None,
) -> dict[str, Any]:
    """Substitute {discovery_query} / {token_login} placeholders in string values."""
    out: dict[str, Any] = {}
    for k, v in (template or {}).items():
        if isinstance(v, str):
            filled = v.replace("{discovery_query}", discovery_query or "")
            if token_login:
                filled = filled.replace("{token_login}", token_login)
            else:
                filled = filled.replace("user:{token_login}", "").replace("{token_login}", "")
            out[k] = filled.strip()
        else:
            out[k] = v
    # GitHub proxy MCP tools use camelCase pagination keys
    if "per_page" in out and "perPage" not in out:
        out["perPage"] = out.pop("per_page")
    if "page_size" in out and "perPage" not in out:
        out["perPage"] = out.pop("page_size")
    return out


async def _await_maybe(fn: Any, kwargs: dict[str, Any]) -> Any:
    """Invoke callable without pruning kwargs; await if needed."""
    check_fn = fn
    while isinstance(check_fn, functools.partial):
        check_fn = check_fn.func
    if hasattr(check_fn, "__call__") and not inspect.isfunction(check_fn):
        try:
            check_fn = check_fn.__call__
        except Exception:
            logger.debug("sources.py: swallowed exception", exc_info=True)

    try:
        if inspect.iscoroutinefunction(check_fn):
            return await fn(**kwargs)
        result = await asyncio.to_thread(fn, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result
    except TypeError:
        # Some local tools accept fewer kwargs — only prune for *non-proxy* paths
        # that raised TypeError. Proxies must never prune; re-raise for proxy.
        raise


async def invoke_proxy(
    plugin_id: str,
    operation_id: str,
    args: dict[str, Any],
) -> Any:
    """Invoke a proxy op with JSON response shape; fail-open to error dict."""
    shaped = {**args, "_response_shape": _RESPONSE_SHAPE}
    try:
        from core.route_registry.execute import ExecuteError, execute_operation

        try:
            result = await execute_operation(
                plugin_id,
                operation_id,
                shaped,
                caller_scopes=None,  # trusted local caller (LOCAL_CLI)
            )
            return _unwrap_envelope(result)
        except ExecuteError as exc:
            if exc.code == "invoke_failed":
                # The callable already ran and raised — a fast-path retry would
                # dispatch the upstream call a second time (side-effect duplication).
                return {"status": "error", "message": exc.message[:500]}
            logger.debug(
                "discovery execute_operation failed (%s/%s): %s — fast_path fallback",
                plugin_id,
                operation_id,
                exc,
            )
    except Exception as exc:
        logger.debug("discovery execute_operation path error: %s", exc)

    # Fast-path without schema validation; never prune kwargs for proxies.
    try:
        from core.context import route_registry

        fn = route_registry.fast_path_callable(
            operation_id, plugin_id=plugin_id, instance_id="default"
        )
        if fn is None:
            return {
                "status": "error",
                "message": f"No fast-path callable for {plugin_id}/{operation_id}",
            }
        try:
            result = await _await_maybe(fn, shaped)
        except TypeError:
            # Last resort: try without _response_shape only (still no signature prune)
            try:
                result = await _await_maybe(fn, dict(args))
            except Exception as exc2:
                return {"status": "error", "message": str(exc2)[:500]}
        except Exception as exc:
            # Proxy callables should not raise; treat as soft error.
            return {"status": "error", "message": str(exc)[:500]}
        # Soft status check — proxies return error dicts instead of raising
        if isinstance(result, dict) and result.get("status") == "error":
            return result
        return _unwrap_envelope(result)
    except Exception as exc:
        return {"status": "error", "message": str(exc)[:500]}


def _merge_repo_lists(
    primary: dict[str, Any],
    secondary: dict[str, Any] | None,
) -> dict[str, Any]:
    """Union repo lists by full_name (case-insensitive).

    Primary wins on conflict, but empty description/readme/topics are filled
    from secondary (allowlist often carries README content).
    """
    out = dict(primary)
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def _ingest(items: list, *, prefer_existing: bool) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("full_name") or "").lower()
            if not name:
                continue
            if name not in by_name:
                by_name[name] = dict(item)
                order.append(name)
                continue
            if prefer_existing:
                # fill holes only
                cur = by_name[name]
                for k, v in item.items():
                    if v in (None, "", [], {}):
                        continue
                    if cur.get(k) in (None, "", [], {}):
                        cur[k] = v
            else:
                by_name[name] = dict(item)

    p_items = primary.get("repos") if isinstance(primary.get("repos"), list) else []
    s_items = (
        secondary.get("repos")
        if isinstance(secondary, dict) and isinstance(secondary.get("repos"), list)
        else []
    )
    _ingest(p_items, prefer_existing=False)
    _ingest(s_items, prefer_existing=True)

    out["repos"] = [by_name[n] for n in order]
    out["count"] = len(out["repos"])
    if secondary and secondary.get("via") == "allowlist":
        out["merged_allowlist"] = True
    return out


async def resolve_github_token_for_discovery() -> str | None:
    """Resolve a GitHub token for discovery: portfolio vault/env, then proxy bearers.

    Live proxies store credentials as ``proxy_github-*`` / key ``bearer`` rather
    than ``portfolio_plugin`` / ``GITHUB_TOKEN``. CLI + local fallbacks need that
    bridge when the portfolio vault key is empty.
    """
    try:
        from plugins.portfolio_plugin.MCPTools.context_tools import _resolve_key

        direct = await _resolve_key("GITHUB_TOKEN")
        if direct:
            return direct
    except Exception:
        logger.debug("sources.py: swallowed exception", exc_info=True)

    try:
        from core.context import vault

        if vault is None:
            return None
        present = await vault.get_all_present_keys()
        # present: {plugin_id: [key_name, ...]}
        if not isinstance(present, dict):
            return None
        for plugin_id, keys in present.items():
            pid = str(plugin_id or "")
            if not pid.lower().startswith("proxy_github"):
                continue
            key_list = keys if isinstance(keys, list) else []
            for kn in ("bearer", "GITHUB_TOKEN", "token", "access_token"):
                if kn not in key_list and kn.lower() not in {str(k).lower() for k in key_list}:
                    # still try common names even if listing is incomplete
                    pass
                try:
                    val = await vault.get(pid, kn)
                except Exception:
                    val = None
                if val:
                    logger.info(
                        "discovery: using vault %s/%s as GitHub token fallback",
                        pid,
                        kn,
                    )
                    return val
            # if keys list empty, still probe bearer
            try:
                val = await vault.get(pid, "bearer")
                if val:
                    return val
            except Exception:
                logger.debug("sources.py: swallowed exception", exc_info=True)
    except Exception as exc:
        logger.debug("discovery proxy-bearer resolve failed: %s", exc)
    return None


async def list_allowlisted_repos(limit: int = 30) -> dict[str, Any]:
    """Fetch meta (+ optional description) for static allowlist / hero repos.

    Uses original-casing refs from the manifest (GitHub paths are case-sensitive
    for some accounts) and the discovery token (proxy bearer bridge) when present.
    """
    import os

    from plugins.portfolio_plugin.MCPTools.context_tools import (
        _fetch_github,
        _static_github_allowlist_detailed,
    )

    try:
        limit_n = max(1, min(int(limit or 30), 100))
    except (TypeError, ValueError):
        limit_n = 30

    owners, _repos_l, original_refs = _static_github_allowlist_detailed()
    # Preserve order, drop dups (case-insensitive)
    refs: list[str] = []
    seen: set[str] = set()
    for ref in original_refs:
        key = ref.lower()
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)

    if not refs and owners:
        return {
            "status": "error",
            "error": "allowlist_owners_only",
            "message": (
                "github_allowlist has owners but no explicit repos, and no GITHUB_TOKEN "
                "to list owned repositories. Add repos to settings.github_allowlist.repos "
                "or set GITHUB_TOKEN / mount proxy_github-*."
            ),
            "owners": sorted(owners),
        }

    token = await resolve_github_token_for_discovery()
    prev = os.environ.get("GITHUB_TOKEN")
    if token:
        os.environ["GITHUB_TOKEN"] = token

    out_repos: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        for ref in refs[:limit_n]:
            try:
                res = await _fetch_github(ref, use=["meta", "readme"], max_chars=8000)
            except Exception as exc:
                errors.append({"ref": ref, "error": str(exc)[:200]})
                continue
            if not isinstance(res, dict) or res.get("status") != "ok":
                errors.append(
                    {
                        "ref": ref,
                        "error": (res or {}).get("error") if isinstance(res, dict) else "fetch_failed",
                        "message": (res or {}).get("message") if isinstance(res, dict) else None,
                    }
                )
                continue
            meta = res.get("meta") if isinstance(res.get("meta"), dict) else {}
            item = {
                "full_name": meta.get("full_name") or ref,
                "description": meta.get("description"),
                "language": meta.get("language"),
                "topics": meta.get("topics") or [],
                "pushed_at": meta.get("pushed_at"),
                "html_url": f"https://github.com/{meta.get('full_name') or ref}",
                "readme": res.get("content"),
                "stars": meta.get("stars"),
                "fork": False,
                "archived": False,
            }
            out_repos.append(item)
    finally:
        if token:
            if prev is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = prev

    if not out_repos and errors:
        return {
            "status": "error",
            "error": "allowlist_fetch_failed",
            "message": "Failed to fetch any allowlisted repos",
            "errors": errors[:10],
            "repos": [],
            "count": 0,
        }
    return {
        "status": "ok",
        "count": len(out_repos),
        "repos": out_repos,
        "via": "allowlist",
        "errors": errors or None,
    }


async def invoke_local_tool(tool_name: str, args: dict[str, Any]) -> Any:
    """Dispatch a local portfolio fallback tool by name."""
    name = (tool_name or "").strip()
    try:
        if name == "list_owned_repos":
            from plugins.portfolio_plugin.MCPTools.context_tools import list_owned_repos

            limit = args.get("limit") or args.get("per_page") or args.get("max_items") or 30
            # Prefer owned-repos when a token is available (incl. proxy bearer bridge).
            token = await resolve_github_token_for_discovery()
            res: dict[str, Any] | None = None
            if token:
                import os

                # Temporarily expose for context_tools._resolve_key env fallback
                prev = os.environ.get("GITHUB_TOKEN")
                os.environ["GITHUB_TOKEN"] = token
                try:
                    res = await list_owned_repos(limit=int(limit))
                finally:
                    if prev is None:
                        os.environ.pop("GITHUB_TOKEN", None)
                    else:
                        os.environ["GITHUB_TOKEN"] = prev
            else:
                res = await list_owned_repos(limit=int(limit))

            # Always merge static allowlist repos so portfolio seeds are not
            # lost when they fall outside the "recently pushed" page.
            allow = await list_allowlisted_repos(limit=int(limit))
            if isinstance(res, dict) and res.get("status") == "ok":
                return _merge_repo_lists(res, allow if isinstance(allow, dict) else None)

            if isinstance(allow, dict) and allow.get("status") == "ok":
                allow["fallback_from"] = "list_owned_repos"
                return allow
            # Prefer the more actionable error
            if isinstance(res, dict) and res.get("status") == "not_configured":
                return {
                    "status": "not_configured",
                    "message": (
                        "No GITHUB_TOKEN for portfolio_plugin and allowlist fetch "
                        "produced no repos. Set vault portfolio_plugin/GITHUB_TOKEN, "
                        "or ensure proxy_github-* bearer exists, or add explicit "
                        "settings.github_allowlist.repos."
                    ),
                    "allowlist": allow,
                }
            return res if isinstance(res, dict) else allow

        if name == "list_allowlisted_repos":
            limit = args.get("limit") or args.get("per_page") or 30
            return await list_allowlisted_repos(limit=int(limit))
        if name == "fetch_repo_insight":
            from plugins.portfolio_plugin.MCPTools.context_tools import fetch_repo_insight

            ref = args.get("ref") or args.get("full_name") or ""
            return await fetch_repo_insight(ref=str(ref), max_chars=int(args.get("max_chars") or 12000))
        if name == "fetch_external_context":
            from plugins.portfolio_plugin.MCPTools.context_tools import fetch_external_context

            return await fetch_external_context(
                kind=str(args.get("kind") or "github"),
                ref=str(args.get("ref") or ""),
                use=args.get("use"),
                max_chars=int(args.get("max_chars") or 12000),
            )
        return {"status": "error", "message": f"Unknown local tool: {name}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)[:500]}


async def fetch_resolved(
    source: ResolvedSource,
    *,
    settings: dict[str, Any] | None = None,
    token_login: str | None = None,
) -> dict[str, Any]:
    """Run one resolved source; always returns a dict envelope."""
    disc = _discovery_settings(settings)
    args = _fill_args_template(
        source.args_template,
        discovery_query=str(disc.get("discovery_query") or ""),
        token_login=token_login,
    )
    if source.plugin_id and source.operation_id:
        raw = await invoke_proxy(source.plugin_id, source.operation_id, args)
        return {
            "capability": source.capability,
            "via": "proxy",
            "plugin_id": source.plugin_id,
            "operation_id": source.operation_id,
            "raw": raw,
            "status": (
                "ok"
                if not (isinstance(raw, dict) and raw.get("status") in ("error", "not_configured"))
                else str(raw.get("status") or "error")
            ),
        }
    if source.local_tool:
        raw = await invoke_local_tool(source.local_tool, args)
        status = "ok"
        if isinstance(raw, dict):
            status = str(raw.get("status") or "ok")
        return {
            "capability": source.capability,
            "via": "local",
            "local_tool": source.local_tool,
            "raw": raw,
            "status": status,
        }
    return {
        "capability": source.capability,
        "via": "none",
        "status": "skipped",
        "raw": None,
    }


async def fetch_all_sources(
    sources: list[ResolvedSource],
    *,
    settings: dict[str, Any] | None = None,
    token_login: str | None = None,
    budget_s: float | None = None,
    concurrency: int = 4,
) -> list[dict[str, Any]]:
    """Fetch all sources under a semaphore + global budget; fail-open per source."""
    disc = _discovery_settings(settings)
    timeout = float(budget_s if budget_s is not None else disc.get("budget_s") or 30)
    sem = asyncio.Semaphore(max(1, int(concurrency)))
    results: list[dict[str, Any]] = []

    async def _one(src: ResolvedSource) -> dict[str, Any]:
        async with sem:
            try:
                return await fetch_resolved(src, settings=settings, token_login=token_login)
            except Exception as exc:
                logger.warning("discovery source %s failed open: %s", src.capability, exc)
                return {
                    "capability": src.capability,
                    "via": "error",
                    "status": "error",
                    "raw": {"status": "error", "message": str(exc)[:500]},
                }

    async def _run() -> None:
        gathered = await asyncio.gather(
            *[_one(s) for s in sources],
            return_exceptions=True,
        )
        for item in gathered:
            if isinstance(item, Exception):
                results.append(
                    {
                        "capability": "unknown",
                        "via": "error",
                        "status": "error",
                        "raw": {"status": "error", "message": str(item)[:500]},
                    }
                )
            else:
                results.append(item)

    try:
        await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.info("discovery source budget %.1fs exceeded; using partial results", timeout)
    return results
