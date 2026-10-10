"""Unity-facing HTTP adapters and MCP callables for semantic world hex operations.

Resolves tenant identity from the validated principal, checks scopes, then loads
the world only if that tenant owns it (``worlds.tenant_id``) before building the
AuthorizedWorld capability. Foreign, unowned and absent worlds are the same
not-found outcome; no projection or semantic service runs after a denial.
"""
from __future__ import annotations

import json
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from core.auth_service import get_auth_service
from core.interfaces.principal import Principal
from core.scope_management import PrincipalKind, ScopeGrant, evaluate_access
from plugins.world_semantic_plugin.asset_index import validate_namespace
from plugins.world_semantic_plugin.hexmath import projection_from_world_row
from plugins.world_semantic_plugin.stores import get_world_for_tenant
from plugins.world_semantic_plugin.world_documents import (
    AuthorizedWorld,
    MAX_IMAGE_BYTES,
    MAX_METADATA_BYTES,
)
from plugins.world_semantic_plugin.world_index import (
    index_world as index_world_service,
    search_world as search_world_service,
)

OWNER = "world_semantic_plugin"
# Dedicated semantic paths only. ``/api/world/none/{world_id}/index`` is NOT
# registered: it would shadow the legacy spatial ingest (``/api/world/{world_id}/index``)
# for a world literally named "none".
SEARCH_WORLD_PATH = "/api/world/none/{world_id}/world/search"
INDEX_WORLD_PATH = "/api/world/none/{world_id}/world/index"

MAX_INDEX_BYTES = MAX_METADATA_BYTES + 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 1024
# Body keys that would let a caller choose identity or filesystem roots.
_FORBIDDEN_BODY_KEYS = frozenset({"tenant_id", "trusted_root"})


class WorldNotFound(LookupError):
    """Raised identically for absent, unowned and other-tenant worlds."""

    def __init__(self) -> None:
        super().__init__("world not found")


async def principal_for_tool() -> Principal | None:
    """Resolve the actual MCP bearer through the public auth boundary; no stdio/default-tenant bypass."""
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    raw = getattr(token, "token", None) if token is not None else None
    return await get_auth_service().principal_from_bearer(raw) if raw else None


def _trusted_world_root(tenant_id: int, world_id: str) -> Path | None:
    """Server-configured snapshot root for this tenant/world (``world_snapshot_roots``), else None."""
    from plugins.world_semantic_plugin.plugin_config import SETTINGS

    roots = SETTINGS.get("world_snapshot_roots", {})
    val = roots.get(str(tenant_id), {}).get(world_id) if isinstance(roots, dict) else None
    return Path(val) if isinstance(val, str) and val else None


async def _authorize_world(
    principal: Principal | None, world_id: str, operation: str, access: str
) -> AuthorizedWorld:
    """Identity → scope → tenant-owned world lookup → projection; fail closed at each step."""
    if principal is None or not principal.subject:
        raise PermissionError("authentication required")
    tenant = principal.tenant_id
    if type(tenant) is not int or tenant < 1:
        raise PermissionError("authenticated principal requires an explicit tenant")
    validate_namespace(tenant, world_id)
    grant = ScopeGrant(
        scopes=list(principal.scopes),
        role=principal.role,
        kind=PrincipalKind.OAUTH_CLIENT,
    )
    decision = evaluate_access(
        grant,
        plugin_id=OWNER,
        tags=[access],
        tool_name=operation,
        path="world_semantic",
        required={f"plugin:{OWNER}", f"group:{OWNER}:{access}"},
    )
    if not decision.allowed:
        raise PermissionError("scope denied")

    world_row = await get_world_for_tenant(world_id, tenant)
    # Re-check ownership evidence in Python too: a row without a matching tenant is not ours.
    if not world_row or world_row.get("tenant_id") != tenant or world_row.get("world_id") != world_id:
        raise WorldNotFound()
    return AuthorizedWorld(tenant_id=tenant, world_id=world_id, projection=projection_from_world_row(world_row))


def _check_body(body: object, world_id: str) -> dict:
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    if _FORBIDDEN_BODY_KEYS & set(body):
        raise ValueError("request cannot choose tenant_id or trusted_root")
    if "world_id" in body and body["world_id"] != world_id:
        raise ValueError("payload world_id must match authorized world")
    return body


async def search_world(world_id: str, query: str, k: int = 10) -> list[dict]:
    """MCP callable: search indexed world hexes by meaning; centers are Unity XZ meters."""
    context = await _authorize_world(await principal_for_tool(), world_id, "search_world", "read")
    return await search_world_service(query, k=k, context=context)


async def index_world(world_id: str, payload: dict) -> dict:
    """MCP callable: index one validated inspect/snapshot hex document into the semantic world index."""
    data = dict(_check_body(payload, world_id))
    context = await _authorize_world(await principal_for_tool(), world_id, "index_world", "write")
    data.setdefault("world_id", world_id)
    return await index_world_service(data, context=context, trusted_root=_trusted_world_root(context.tenant_id, world_id))


async def _read_body(request: Request, limit: int) -> dict | None:
    chunks, count = [], 0
    async for chunk in request.stream():
        count += len(chunk)
        if count > limit:
            return None
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks)) if chunks else {}
    except (ValueError, UnicodeError):
        raise ValueError("invalid JSON body") from None


async def _http(request: Request, *, indexing: bool) -> JSONResponse:
    try:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return JSONResponse({"status": "error", "error": "authentication_required"}, status_code=401)
        principal = await get_auth_service().principal_from_bearer(header[7:].strip())
        if principal is None:
            return JSONResponse({"status": "error", "error": "authentication_required"}, status_code=401)
        world_id = request.path_params.get("world_id", "")
        operation, access = ("index_world", "write") if indexing else ("search_world", "read")
        context = await _authorize_world(principal, world_id, operation, access)

        body = await _read_body(request, MAX_INDEX_BYTES if indexing else MAX_METADATA_BYTES)
        if body is None:
            return JSONResponse({"status": "error", "error": "payload_too_large"}, status_code=413)
        body = _check_body(body, world_id)
        if indexing:
            data = dict(body)
            data.setdefault("world_id", world_id)
            result = await index_world_service(
                data, context=context, trusted_root=_trusted_world_root(context.tenant_id, world_id)
            )
            return JSONResponse(result, status_code=202 if result.get("status") == "queued" else 200)
        if set(body) - {"query", "k"}:
            raise ValueError("unknown search parameter")
        hits = await search_world_service(body.get("query"), k=body.get("k", 10), context=context)
        return JSONResponse({"status": "ok", "world_id": world_id, "results": hits})
    except WorldNotFound:
        return JSONResponse({"status": "error", "error": "world_not_found"}, status_code=404)
    except PermissionError as exc:
        if str(exc) == "authentication required":
            return JSONResponse({"status": "error", "error": "authentication_required"}, status_code=401)
        return JSONResponse({"status": "error", "error": "scope_or_tenant_denied"}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"status": "error", "error": "invalid_world_input", "detail": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({"status": "error", "error": "world_service_failed"}, status_code=503)


async def search_world_http(request: Request) -> JSONResponse:
    """POST ``{query, k?}``; self-authenticates the bearer, then requires a tenant-owned world."""
    return await _http(request, indexing=False)


async def index_world_http(request: Request) -> JSONResponse:
    """POST one inspect/snapshot envelope; self-authenticates the bearer, then requires a tenant-owned world."""
    return await _http(request, indexing=True)


def register_world_routes() -> None:
    """Register authenticated Unity-facing HTTP routes for world semantic v2."""
    from core.context import http_route_registry
    from core.http_route_registry import AuthPolicy

    for path, handler, name in (
        (SEARCH_WORLD_PATH, search_world_http, "search_world"),
        (INDEX_WORLD_PATH, index_world_http, "index_world"),
    ):
        http_route_registry.register_http_route(
            path, handler, methods=["POST"], name=name, owner=OWNER, auth_policy=AuthPolicy.NONE
        )
