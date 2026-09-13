"""Server-side Jules multi-role review fleet tools.

Roles are **optional** — omit / empty / null means build nothing and fire nothing
(no fixed 2+2+1 fleet). The config builder lives on the Whiskers Agent server under
``plugins.jules_plugin.review_fleet`` (not a client-side CLI skill).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.context import mcp
from core.proxy.proxy_manager import _safe_async_client
from plugins.jules_plugin.review_fleet.templates import (
    ALL_ROLES,
    build_configs,
    parse_roles,
)

logger = logging.getLogger("whiskers.plugins.jules_plugin")

_JULES_SESSIONS_URL = "https://jules.googleapis.com/v1alpha/sessions"


def _normalize_roles(roles: str | list[str] | None) -> list[str]:
    """Parse optional roles; empty → no runners (never auto-fleet)."""
    if roles is None:
        return []
    if isinstance(roles, list):
        raw = ",".join(str(r).strip() for r in roles if str(r).strip())
    else:
        raw = str(roles).strip()
    if not raw:
        return []
    return parse_roles(raw, allow_custom=True)


def _resolve_git_defaults(
    repo: str | None,
    branch: str | None,
    base: str | None,
    mode: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Best-effort git defaults when running on a server with a product checkout.

    Missing values stay None so the caller can require them only when needed.
    """
    err: str | None = None
    try:
        from plugins.jules_plugin.review_fleet.driver import (
            current_branch,
            current_repo,
            detect_default_branch,
        )
    except Exception as exc:  # pragma: no cover - import always available in-tree
        return repo, branch, base, f"git helpers unavailable: {exc}"

    if not repo:
        repo = current_repo()
    if not branch:
        branch = current_branch()
    if mode == "diff" and not base:
        base = detect_default_branch()
    return repo, branch, base, err


async def _auth_headers() -> dict[str, str]:
    """Jules API key headers via plugin auth registry."""
    from core.plugin_loader.plugin_registry import get_registry

    return await get_registry().auth.get_auth_headers("jules_plugin")


async def _post_session(body: dict[str, Any]) -> dict[str, Any]:
    """POST one CreateSession to Jules; return shaped ok/error dict."""
    headers = await _auth_headers()
    headers = {**headers, "Content-Type": "application/json", "Accept": "application/json"}

    async def _req():
        async with _safe_async_client(timeout=60.0) as client:
            return await client.post(_JULES_SESSIONS_URL, headers=headers, json=body)

    try:
        resp = await _req()
    except Exception as exc:
        logger.exception("jules fire_review_fleet: create_session transport error")
        return {"status": "error", "error": str(exc)}

    text = resp.text
    try:
        data = resp.json() if text else {}
    except json.JSONDecodeError as exc:
        logger.debug("jules fire_review_fleet: non-JSON response body, falling back to raw text: %s", exc)
        data = {"raw": text[:2000] if text else ""}

    if resp.status_code >= 400:
        return {
            "status": "error",
            "http_status": resp.status_code,
            "error": data if isinstance(data, dict) else {"message": str(data)},
        }
    return {"status": "ok", "session": data}


def _session_body_from_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Map fleet config → Jules CreateSession JSON body (sourceContext as object)."""
    sc = cfg.get("sourceContext")
    if isinstance(sc, str):
        try:
            sc = json.loads(sc)
        except json.JSONDecodeError:
            sc = None
    body: dict[str, Any] = {
        "prompt": cfg["prompt"],
        "title": cfg.get("title"),
        "sourceContext": sc,
        "requirePlanApproval": bool(cfg.get("requirePlanApproval") or False),
    }
    am = cfg.get("automationMode")
    if am and am != "AUTOMATION_MODE_UNSPECIFIED":
        body["automationMode"] = am
    # Drop nulls Jules may reject
    return {k: v for k, v in body.items() if v is not None}


@mcp.tool(
    name="julesbuild_review_fleet",
    title="build_review_fleet",
    tags={"jules_plugin", "read", "sessions"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def julesbuild_review_fleet(
    roles: str = "",
    mode: str = "full",
    base: str = "",
    repo: str = "",
    branch: str = "",
    path: str = ".",
    frontend_path: str = "",
    backend_path: str = "",
    docs_path: str = "",
    require_plan_approval: bool = False,
    automation_mode: str = "AUTOMATION_MODE_UNSPECIFIED",
) -> dict[str, Any]:
    """Build Jules multi-role review configs on the **server** (no network).

    **Roles are optional.** Omit, pass ``\"\"``, or pass nothing to get
    ``configs: []`` — there is no automatic 2+2+1 fleet. Pass a comma list
    (``frontend-a,backend-b``) or ``all`` for the classic five roles.

    Returns fully expanded multi-line ``prompt`` strings (never bare role
    labels). Call ``julescreate_session`` per config, or use
    ``julesfire_review_fleet`` to create sessions server-side.
    """
    try:
        role_list = _normalize_roles(roles)
    except ValueError as exc:
        return {"status": "error", "error": str(exc), "valid_roles": list(ALL_ROLES)}

    if not role_list:
        return {
            "status": "ok",
            "roles": [],
            "configs": [],
            "message": "No roles requested — nothing to build. Pass roles=all or a comma list.",
        }

    mode_n = (mode or "full").strip().lower()
    if mode_n not in ("full", "diff"):
        return {"status": "error", "error": "mode must be 'full' or 'diff'"}

    repo_v = (repo or "").strip() or None
    branch_v = (branch or "").strip() or None
    base_v = (base or "").strip() or None
    repo_v, branch_v, base_v, _ = _resolve_git_defaults(repo_v, branch_v, base_v, mode_n)

    if not repo_v:
        return {
            "status": "error",
            "error": "repo is required (owner/name). Pass repo= or run where git origin is GitHub.",
        }
    if not branch_v:
        return {
            "status": "error",
            "error": "branch is required (Jules startingBranch). Pass branch= explicitly on the server.",
        }
    if mode_n == "diff":
        if not base_v:
            return {
                "status": "error",
                "error": "diff mode requires base (target branch). Pass base=main or similar.",
            }
        if base_v == branch_v:
            return {
                "status": "error",
                "error": f"diff mode: base '{base_v}' equals branch '{branch_v}'",
            }

    default_path = (path or ".").strip() or "."
    fe = (frontend_path or default_path).strip() or default_path
    be = (backend_path or default_path).strip() or default_path
    docs = (docs_path or default_path).strip() or default_path

    configs = build_configs(
        role_list,
        repo_v,
        branch_v,
        require_plan_approval,
        automation_mode or "AUTOMATION_MODE_UNSPECIFIED",
        fe,
        be,
        docs,
        mode=mode_n,
        base_branch=base_v if mode_n == "diff" else None,
    )
    return {
        "status": "ok",
        "roles": role_list,
        "mode": mode_n,
        "repo": repo_v,
        "branch": branch_v,
        "base": base_v if mode_n == "diff" else None,
        "configs": configs,
        "count": len(configs),
        "hint": "Pass each configs[i] into julescreate_session, or call julesfire_review_fleet with the same args.",
    }


@mcp.tool(
    name="julesfire_review_fleet",
    title="fire_review_fleet",
    tags={"jules_plugin", "write_update", "sessions", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def julesfire_review_fleet(
    roles: str = "",
    mode: str = "full",
    base: str = "",
    repo: str = "",
    branch: str = "",
    path: str = ".",
    frontend_path: str = "",
    backend_path: str = "",
    docs_path: str = "",
    require_plan_approval: bool = False,
    automation_mode: str = "AUTOMATION_MODE_UNSPECIFIED",
) -> dict[str, Any]:
    """Build **and create** Jules review sessions on the **server**.

    **Roles are optional.** Empty roles → ``sessions: []`` and no API calls
    (safe no-op). Non-empty roles expand full multi-line prompts via the server
    ``review_fleet`` templates, then POST ``CreateSession`` for each role.

    Prefer this over client-side ``driver.py`` when talking to Whiskers Agent MCP.
    """
    built = await julesbuild_review_fleet(
        roles=roles,
        mode=mode,
        base=base,
        repo=repo,
        branch=branch,
        path=path,
        frontend_path=frontend_path,
        backend_path=backend_path,
        docs_path=docs_path,
        require_plan_approval=require_plan_approval,
        automation_mode=automation_mode,
    )
    if built.get("status") != "ok":
        return built

    configs = built.get("configs") or []
    if not configs:
        return {
            "status": "ok",
            "roles": [],
            "sessions": [],
            "fired": 0,
            "message": "No roles requested — nothing fired. Pass roles=all or a comma list to create sessions.",
        }

    sessions: list[dict[str, Any]] = []
    for cfg in configs:
        body = _session_body_from_config(cfg)
        result = await _post_session(body)
        entry = {
            "role": cfg.get("role"),
            "title": cfg.get("title"),
            "result": result,
        }
        if result.get("status") == "ok":
            sess = result.get("session") or {}
            entry["sessionId"] = sess.get("id") or sess.get("name")
            entry["url"] = sess.get("url")
        sessions.append(entry)

    ok_n = sum(1 for s in sessions if (s.get("result") or {}).get("status") == "ok")
    err_n = len(sessions) - ok_n
    return {
        "status": "ok" if err_n == 0 else ("partial" if ok_n else "error"),
        "roles": built.get("roles") or [],
        "mode": built.get("mode"),
        "repo": built.get("repo"),
        "branch": built.get("branch"),
        "base": built.get("base"),
        "fired": ok_n,
        "failed": err_n,
        "sessions": sessions,
        "configs": configs,
    }
