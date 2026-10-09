"""Unity-facing HTTP adapters and MCP callables for semantic world hex operations.

Resolves tenant identity from validated principal, checks world authorization,
loads established projection, and constructs AuthorizedWorld capability.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from core.auth_service import get_auth_service
from core.interfaces.principal import Principal
from core.scope_management import PrincipalKind, ScopeGrant, evaluate_access
from plugins.world_semantic_plugin.asset_index import validate_namespace
from plugins.world_semantic_plugin.hexmath import projection_from_world_row
from plugins.world_semantic_plugin.stores import get_world
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
SEARCH_WORLD_PATH = "/api/world/none/{world_id}/world/search"
SEARCH_WORLD_ALIAS_PATH = "/api/world/none/{world_id}/search"
INDEX_WORLD_PATH = "/api/world/none/{world_id}/world/index"
INDEX_WORLD_ALIAS_PATH = "/api/world/none/{world_id}/index"

MAX_INDEX_BYTES = MAX_METADATA_BYTES + 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 1024


async def principal_for_tool() -> Principal | None:
    """Resolve the actual MCP bearer through the public auth boundary; no stdio/default-tenant bypass."""
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    raw = getattr(token, "token", None) if token is not None else None
    return await get_auth_service().principal_from_bearer(raw) if raw else None


def _trusted_world_root(tenant_id: int, world_id: str) -> Path | None:
    from plugins.world_semantic_plugin.plugin_config import SETTINGS

    for key in ("world_snapshot_roots", "world_ingestion_roots", "asset_ingestion_roots"):
        roots = SETTINGS.get(key, {})
        if isinstance(roots, dict):
            val = roots.get(str(tenant_id), {}).get(world_id)
            if isinstance(val, str) and val:
                return Path(val)
    return None


async def _authorize_world(
    principal: Principal | None, world_id: str, operation: str, access: str
) -> AuthorizedWorld:
    if principal is None or not principal.subject:
        raise PermissionError("authentication required")
    if type(principal.tenant_id) is not int or principal.tenant_id < 1:
        raise PermissionError("authenticated principal requires an explicit tenant")
    validate_namespace(principal.tenant_id, world_id)
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

    world_row = await get_world(world_id)
    if not world_row:
        raise PermissionError(f"world '{world_id}' not found or unauthorized")

    proj = projection_from_world_row(world_row)
    return AuthorizedWorld(tenant_id=principal.tenant_id, world_id=world_id, projection=proj)


async def search_world(world_id: str, query: str, k: int = 10) -> list[dict]:
    """MCP callable: search indexed world hexes by meaning; centers are Unity XZ meters."""
    context = await _authorize_world(await principal_for_tool(), world_id, "search_world", "read")
    return await search_world_service(query, k=k, context=context)


async def index_world(world_id: str, payload: dict) -> dict:
    """MCP callable: index one validated inspect/snapshot hex document into the semantic world index."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dictionary")
    if "tenant_id" in payload:
        raise ValueError("request cannot choose or spoof tenant_id")
    if "trusted_root" in payload:
        raise ValueError("request cannot specify trusted_root")
    if "world_id" in payload and payload["world_id"] != world_id:
        raise ValueError("payload world_id must match authorized world")
    context = await _authorize_world(await principal_for_tool(), world_id, "index_world", "write")
    data = dict(payload)
    if "world_id" not in data:
        data["world_id"] = world_id
    root = _trusted_world_root(context.tenant_id, world_id)
    return await index_world_service(data, context=context, trusted_root=root)


async def search_world_http(request: Request) -> JSONResponse:
    """POST text query to search indexed world hexes; self-authenticates bearer."""
    try:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return JSONResponse({"status": "error", "error": "authentication_required"}, status_code=401)
        principal = await get_auth_service().principal_from_bearer(header[7:].strip())
        if principal is None:
            return JSONResponse({"status": "error", "error": "authentication_required"}, status_code=401)
        world_id = request.path_params.get("world_id", "")
        context = await _authorize_world(principal, world_id, "search_world", "read")

        chunks, count = [], 0
        async for chunk in request.stream():
            count += len(chunk)
            if count > MAX_METADATA_BYTES:
                return JSONResponse({"status": "error", "error": "payload_too_large"}, status_code=413)
            chunks.append(chunk)

        try:
            body = json.loads(b"".join(chunks)) if chunks else {}
        except (ValueError, UnicodeError):
            raise ValueError("invalid JSON body") from None

        if not isinstance(body, dict) or "tenant_id" in body or "trusted_root" in body:
            raise ValueError("invalid search request shape")
        if set(body) - {"query", "k"}:
            raise ValueError("unknown search parameter")

        query = body.get("query")
        k = body.get("k", 10)
        hits = await search_world_service(query, k=k, context=context)
        return JSONResponse({"status": "ok", "world_id": world_id, "results": hits, "hexes": hits})
    except PermissionError:
        return JSONResponse({"status": "error", "error": "scope_or_world_denied"}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"status": "error", "error": "invalid_world_input", "detail": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({"status": "error", "error": "world_service_failed"}, status_code=503)


async def index_world_http(request: Request) -> JSONResponse:
    """POST inspect/snapshot envelope to index a world hex; self-authenticates bearer."""
    try:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return JSONResponse({"status": "error", "error": "authentication_required"}, status_code=401)
        principal = await get_auth_service().principal_from_bearer(header[7:].strip())
        if principal is None:
            return JSONResponse({"status": "error", "error": "authentication_required"}, status_code=401)
        world_id = request.path_params.get("world_id", "")
        context = await _authorize_world(principal, world_id, "index_world", "write")

        chunks, count = [], 0
        async for chunk in request.stream():
            count += len(chunk)
            if count > MAX_INDEX_BYTES:
                return JSONResponse({"status": "error", "error": "payload_too_large"}, status_code=413)
            chunks.append(chunk)

        try:
            body = json.loads(b"".join(chunks)) if chunks else {}
        except (ValueError, UnicodeError):
            raise ValueError("invalid JSON body") from None

        if not isinstance(body, dict):
            raise ValueError("invalid JSON body shape")
        if "tenant_id" in body and (type(body["tenant_id"]) is not int or body["tenant_id"] != context.tenant_id):
            raise ValueError("payload tenant mismatch")
        if "trusted_root" in body:
            raise ValueError("request cannot specify trusted_root")
        if "world_id" in body and body["world_id"] != world_id:
            raise ValueError("payload world_id must match authorized world")

        data = dict(body)
        if "world_id" not in data:
            data["world_id"] = world_id

        root = _trusted_world_root(context.tenant_id, world_id)
        result = await index_world_service(data, context=context, trusted_root=root)
        return JSONResponse(result, status_code=202 if result.get("status") == "queued" else 200)
    except PermissionError:
        return JSONResponse({"status": "error", "error": "scope_or_world_denied"}, status_code=403)
    except ValueError as exc:
        return JSONResponse({"status": "error", "error": "invalid_world_input", "detail": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({"status": "error", "error": "world_service_failed"}, status_code=503)


def register_world_routes() -> None:
    """Register authenticated Unity-facing HTTP routes for world semantic v2."""
    from core.context import http_route_registry
    from core.http_route_registry import AuthPolicy

    for path, handler, name in (
        (SEARCH_WORLD_PATH, search_world_http, "search_world"),
        (SEARCH_WORLD_ALIAS_PATH, search_world_http, "search_world__alias"),
        (INDEX_WORLD_PATH, index_world_http, "index_world"),
        (INDEX_WORLD_ALIAS_PATH, index_world_http, "index_world__alias"),
    ):
        http_route_registry.register_http_route(
            path, handler, methods=["POST"], name=name, owner=OWNER, auth_policy=AuthPolicy.NONE
        )
