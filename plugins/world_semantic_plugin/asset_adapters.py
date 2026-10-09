"""Thin authenticated MCP callables/HTTP adapters; final tool/route discovery is S04-owned."""
from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from core.auth_service import get_auth_service
from core.interfaces.principal import Principal
from core.scope_management import PrincipalKind, ScopeGrant, evaluate_access
from plugins.world_semantic_plugin.asset_index import (
    MAX_INDEX_BYTES, MAX_MEDIA_BYTES, index_assets as index_assets_service,
    search_assets as search_assets_service, validate_namespace,
)

SEARCH_PATH = "/api/world/none/{world_id}/assets/search"
INDEX_PATH = "/api/world/none/{world_id}/assets/index"
OWNER = "world_semantic_plugin"


async def principal_for_tool() -> Principal | None:
    """Resolve the actual MCP bearer through the public auth boundary; no stdio/default-tenant bypass."""
    from fastmcp.server.dependencies import get_access_token
    token = get_access_token()
    raw = getattr(token, "token", None) if token is not None else None
    return await get_auth_service().principal_from_bearer(raw) if raw else None


def _authorize(principal: Principal | None, world_id: str, operation: str, access: str) -> int:
    if principal is None or not principal.subject:
        raise PermissionError("authentication required")
    if type(principal.tenant_id) is not int or principal.tenant_id < 1:
        raise PermissionError("authenticated principal requires an explicit tenant")
    validate_namespace(principal.tenant_id, world_id)
    grant = ScopeGrant(scopes=list(principal.scopes), role=principal.role, kind=PrincipalKind.OAUTH_CLIENT)
    decision = evaluate_access(grant, plugin_id=OWNER, tags=[access], tool_name=operation,
                               path="world_assets", required={f"plugin:{OWNER}", f"group:{OWNER}:{access}"})
    if not decision.allowed:
        raise PermissionError("scope denied")
    # World names are private library namespaces *within* this verified tenant, not global worlds.
    return principal.tenant_id


def _trusted_root(tenant_id: int, world_id: str) -> Path:
    from plugins.world_semantic_plugin.plugin_config import SETTINGS
    roots = SETTINGS.get("asset_ingestion_roots", {})
    value = roots.get(str(tenant_id), {}).get(world_id) if isinstance(roots, dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError("no trusted asset ingestion root configured for this tenant/world")
    return Path(value)


async def search_assets(world_id: str, query: str | None = None, image: dict | None = None,
                        kind: str | None = None, tags: list[str] | None = None, k: int = 10) -> list[dict]:
    """MCP callable: authenticated tenant inferred from bearer; query XOR inline base64 PNG/JPEG."""
    tenant = _authorize(await principal_for_tool(), world_id, "search_assets", "read")
    return await search_assets_service(query, image=image, tenant_id=tenant, world_id=world_id,
                                       kind=kind, tags=tags, k=k)


async def index_assets(world_id: str, index: dict) -> dict:
    """MCP callable: stage craft-v3 JSON using only the server-configured trusted ingestion root."""
    tenant = _authorize(await principal_for_tool(), world_id, "index_assets", "write")
    return await index_assets_service(index, tenant_id=tenant, world_id=world_id,
                                      trusted_root=_trusted_root(tenant, world_id))


async def _http(request: Request, *, indexing: bool) -> JSONResponse:
    try:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return JSONResponse({"status": "error", "error": "authentication_required"}, status_code=401)
        principal = await get_auth_service().principal_from_bearer(header[7:].strip())
        if principal is None:
            return JSONResponse({"status": "error", "error": "authentication_required"}, status_code=401)
        world = request.path_params["world_id"]
        tenant = _authorize(principal, world, "index_assets" if indexing else "search_assets", "write" if indexing else "read")
        maximum = MAX_INDEX_BYTES if indexing else (MAX_MEDIA_BYTES + 2) // 3 * 4 + MAX_INDEX_BYTES
        chunks, count = [], 0
        async for chunk in request.stream():
            count += len(chunk)
            if count > maximum:
                return JSONResponse({"status": "error", "error": "payload_too_large"}, status_code=413)
            chunks.append(chunk)
        import json
        try:
            body = json.loads(b"".join(chunks))
        except (ValueError, UnicodeError):
            raise ValueError("invalid JSON body") from None
        if not isinstance(body, dict) or "tenant_id" in body or "trusted_root" in body:
            raise ValueError("invalid asset request shape")
        if indexing:
            result = await index_assets_service(body, tenant_id=tenant, world_id=world,
                                                trusted_root=_trusted_root(tenant, world))
            return JSONResponse(result, status_code=202 if result["status"] == "enqueued" else 200)
        if set(body) - {"query", "image", "kind", "tags", "k"}:
            raise ValueError("unknown search parameter")
        hits = await search_assets_service(tenant_id=tenant, world_id=world, **body)
        return JSONResponse({"status": "ok", "assets": hits})
    except PermissionError:
        return JSONResponse({"status": "error", "error": "scope_or_tenant_denied"}, status_code=403)
    except ValueError:
        return JSONResponse({"status": "error", "error": "invalid_asset_input"}, status_code=400)
    except Exception:
        return JSONResponse({"status": "error", "error": "asset_service_failed"}, status_code=503)


async def search_assets_http(request: Request) -> JSONResponse:
    """POST inline text/image search; handler self-authenticates bearer for Unity/API-key clients."""
    return await _http(request, indexing=False)


async def index_assets_http(request: Request) -> JSONResponse:
    """POST craft-v3 index JSON; only scoped, tenant-bearing principals can enqueue work."""
    return await _http(request, indexing=True)


def register_asset_routes() -> None:
    """S04 wiring seam: register self-authenticated POST endpoints, never public indexing/search."""
    from core.context import http_route_registry
    from core.http_route_registry import AuthPolicy
    for path, handler, name in ((SEARCH_PATH, search_assets_http, "search_assets"),
                                (INDEX_PATH, index_assets_http, "index_assets")):
        http_route_registry.register_http_route(path, handler, methods=["POST"], name=name,
                                                owner=OWNER, auth_policy=AuthPolicy.NONE)
