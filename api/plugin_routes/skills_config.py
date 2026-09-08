"""Plugin skills + config + logs REST handlers.

Part of the ``api.plugin_routes`` package (split for maintainability).
"""
from __future__ import annotations

import asyncio
import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from utils.error_response import safe_error_response

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from db_layer.plugin_registry_store import PluginMetaKey
from db_layer.telemetry_store import telemetry_store
from core.plugin_loader.config_file_store import load_base_config
from core.plugin_loader.skill_file_store import skill_content_hash

from api.plugin_routes import deps as _deps
from api.plugin_routes.deps import (  # noqa: F401 — re-exported names used below
    mcp,
    oauth_relay,
    vault,
    tool_visibility,
    get_async_session,
    get_registry,
    _plugin_loader,
    load_skill_from_disk,
    _db_registry,
    _refresh_live_plugin_skills,
    _skill_row_for,
    parse_tier,
    _build_plugin_payload,
)

logger = logging.getLogger("whiskers")

@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/skills",
    methods=["GET"],
    name="api_get_plugin_skills",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:read",),
    owner="api.plugins",
)
async def get_plugin_skills(request: Request) -> Response:
    """Return skill files (key -> content) for a plugin. Merges declared from manifest + DB overrides."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)
    declared = []
    try:
        # declared paths come from the relayed manifest (populated at load)
        m = _plugin_loader._relay_manifests.get(plugin_id) or {}
        declared = list(m.get("skills", []) or [])
    except Exception as exc:
        logger.debug("swallowed exception (non-fatal): %s", exc)
    db_skills = await _db_registry.get_skills(plugin_id)
    # Build response list; include DB + declared (even if not yet in DB after first load)
    keys = set(declared) | set((db_skills or {}).keys())
    skills_list = [
        _skill_row_for(plugin_id, k, (db_skills or {}).get(k, ""), k in declared)
        for k in sorted(keys)
    ]
    return JSONResponse({
        "plugin_id": plugin_id,
        "skills": skills_list,
        "declared_from_manifest": declared,
    }, status_code=200)


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/skills",
    methods=["PUT"],
    name="api_put_plugin_skill",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def put_plugin_skill(request: Request) -> Response:
    """Create or update a skill file (key + content). Persists to meta.skills and refreshes live registry."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("put_plugin_skill: failed to parse JSON body: %s", exc)
        body = {}
    key = (body or {}).get("key")
    content = (body or {}).get("content", "")
    if not key or not isinstance(key, str):
        return JSONResponse({"error": "key (string) is required in body"}, status_code=400)
    record = await _db_registry.get_by_id(plugin_id)
    if not record:
        return JSONResponse({"error": "plugin not found"}, status_code=404)
    try:
        await _db_registry.set_skill(plugin_id, key, content)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"set_skill {plugin_id}/{key}")

    await _refresh_live_plugin_skills(plugin_id)
    row = _skill_row_for(plugin_id, key, content or "", declared=False)
    try:
        m = _plugin_loader._relay_manifests.get(plugin_id) or {}
        row["declared"] = key in list(m.get("skills", []) or [])
    except Exception as exc:
        logger.debug("swallowed exception (non-fatal): %s", exc)
    return JSONResponse({
        "plugin_id": plugin_id,
        "key": key,
        "saved": True,
        "content_hash": row.get("content_hash"),
        "fs_content_hash": row.get("fs_content_hash"),
        "in_sync": row.get("in_sync"),
    }, status_code=200)


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/skills",
    methods=["DELETE"],
    name="api_delete_plugin_skill",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def delete_plugin_skill(request: Request) -> Response:
    """Delete a skill file by key. Updates DB + live registry."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("delete_plugin_skill: failed to parse JSON body: %s", exc)
        body = {}
    key = (body or {}).get("key")
    if not key or not isinstance(key, str):
        return JSONResponse({"error": "key (string) is required in body"}, status_code=400)
    record = await _db_registry.get_by_id(plugin_id)
    if not record:
        return JSONResponse({"error": "plugin not found"}, status_code=404)
    try:
        await _db_registry.delete_skill(plugin_id, key)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"delete_skill {plugin_id}/{key}")

    await _refresh_live_plugin_skills(plugin_id)
    return JSONResponse({"plugin_id": plugin_id, "key": key, "deleted": True}, status_code=200)


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/skills/reload",
    methods=["POST"],
    name="api_reload_plugin_skills",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def reload_plugin_skills(request: Request) -> Response:
    """Load skill file(s) from disk into DB meta.skills and refresh the live registry.

    Body:
      - ``{ "key": "skills/foo.md" }`` — reload one skill
      - ``{}`` or no key — reload every path currently listed (declared ∪ DB keys)
        that exists on disk

    Returns per-key results with ``content_hash`` / ``fs_content_hash`` for validation.
    """
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)
    record = await _db_registry.get_by_id(plugin_id)
    if not record:
        return JSONResponse({"error": "plugin not found"}, status_code=404)
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("reload_plugin_skills: failed to parse JSON body: %s", exc)
        body = {}
    key = (body or {}).get("key") if isinstance(body, dict) else None

    declared: list[str] = []
    try:
        m = _plugin_loader._relay_manifests.get(plugin_id) or {}
        declared = list(m.get("skills", []) or [])
    except Exception as exc:
        logger.debug("swallowed exception (non-fatal): %s", exc)
    db_skills = await _db_registry.get_skills(plugin_id) or {}

    if key is not None:
        if not isinstance(key, str) or not key.strip():
            return JSONResponse({"error": "key must be a non-empty string when provided"}, status_code=400)
        keys = [key.strip()]
    else:
        keys = sorted(set(declared) | set(db_skills.keys()))
        if not keys:
            # Fall back to manifest-declared only (already empty) — nothing to reload
            return JSONResponse({
                "plugin_id": plugin_id,
                "reloaded": [],
                "failed": [],
                "message": "no skill keys to reload",
            }, status_code=200)

    reloaded: list[dict] = []
    failed: list[dict] = []
    for k in keys:
        disk = load_skill_from_disk(plugin_id, k)
        if not disk.get("ok"):
            failed.append({
                "key": k,
                "error": disk.get("error") or "load failed",
                "on_disk": bool(disk.get("on_disk")),
            })
            continue
        content = disk.get("content") or ""
        try:
            await _db_registry.set_skill(plugin_id, k, content)
        except Exception as exc:
            logger.error("reload set_skill %s/%s: %s", plugin_id, k, exc)
            failed.append({"key": k, "error": str(exc), "on_disk": True})
            continue
        reloaded.append({
            "key": k,
            "content_hash": disk.get("content_hash"),
            "fs_content_hash": disk.get("content_hash"),
            "in_sync": True,
            "declared": k in declared,
            "chars": len(content),
        })

    if reloaded:
        await _refresh_live_plugin_skills(plugin_id)

    status = 200 if reloaded or not failed else 404 if len(failed) == len(keys) else 207
    # Single-key not-found → 404; mixed bulk → 200 with failed list (easier for UI)
    if key is not None and not reloaded and failed:
        status = 404
    return JSONResponse({
        "plugin_id": plugin_id,
        "reloaded": reloaded,
        "failed": failed,
    }, status_code=status)


# ---------------------------------------------------------------------------
# Logs (tool-call history for a plugin)
# GET /api/plugins/{plugin_id}/logs?page=&per_page=&status=ok|error
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/logs",
    methods=["GET"],
    name="api_get_plugin_logs",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:read",),
    owner="api.plugins",
)
async def get_plugin_logs(request: Request) -> Response:
    """Paginated tool-call history for a plugin (status filter, newest first)."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)

    try:
        page = max(int(request.query_params.get("page", "1")), 1)
    except ValueError:
        page = 1
    try:
        per_page = min(max(int(request.query_params.get("per_page", "25")), 1), 100)
    except ValueError:
        per_page = 25
    status = request.query_params.get("status") or None
    if status not in (None, "ok", "error"):
        return JSONResponse({"error": "status must be 'ok' or 'error'"}, status_code=400)

    try:
        items, total = await telemetry_store.get_plugin_logs(plugin_id, page, per_page, status)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"get_plugin_logs {plugin_id}")

    return JSONResponse({
        "plugin_id": plugin_id,
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
    })


# ---------------------------------------------------------------------------
# Config (console-editable override of a plugin's config.json)
# GET    /api/plugins/{plugin_id}/config
# PUT    /api/plugins/{plugin_id}/config          body: {config: {...}}
# DELETE /api/plugins/{plugin_id}/config
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/config",
    methods=["GET"],
    name="api_get_plugin_config",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:read",),
    owner="api.plugins",
)
async def get_plugin_config(request: Request) -> Response:
    """Return on-disk base config.json + console-edited DB override + effective merge."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)

    base, filename = await asyncio.to_thread(load_base_config, plugin_id)
    override = await _db_registry.get_config_override(plugin_id)
    effective = override if override is not None else (base or {})

    return JSONResponse({
        "plugin_id": plugin_id,
        "config_filename": filename,
        "base": base,
        "override": override,
        "effective": effective,
    })


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/config",
    methods=["PUT"],
    name="api_put_plugin_config",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def put_plugin_config(request: Request) -> Response:
    """Save a console-edited config override to meta.config. Applies on next plugin reload."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("put_plugin_config: failed to parse JSON body: %s", exc)
        body = {}
    config = (body or {}).get("config")
    if not isinstance(config, dict):
        return JSONResponse({"error": "config (object) is required in body"}, status_code=400)

    record = await _db_registry.get_by_id(plugin_id)
    if not record:
        return JSONResponse({"error": "plugin not found"}, status_code=404)

    try:
        await _db_registry.set_config_override(plugin_id, config)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"set_config_override {plugin_id}")

    return JSONResponse({"plugin_id": plugin_id, "saved": True})


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/config",
    methods=["DELETE"],
    name="api_delete_plugin_config",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def delete_plugin_config(request: Request) -> Response:
    """Clear the config override, reverting to the on-disk base config.json."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)
    try:
        await _db_registry.clear_config_override(plugin_id)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"clear_config_override {plugin_id}")
    return JSONResponse({"plugin_id": plugin_id, "deleted": True})


# ---------------------------------------------------------------------------
