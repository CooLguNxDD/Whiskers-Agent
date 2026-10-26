"""Thin authenticated MCP callables/HTTP adapters; final tool/route discovery is S04-owned."""
from __future__ import annotations

import logging
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from core.auth_service import get_auth_service
from core.interfaces.principal import Principal
from core.scope_management import PrincipalKind, ScopeGrant, evaluate_access
from plugins.world_semantic_plugin import asset_packs
from plugins.world_semantic_plugin.asset_index import (
    MAX_INDEX_BYTES, MAX_MEDIA_BYTES, index_assets as index_assets_service,
    search_assets as search_assets_service, validate_namespace,
)

SEARCH_PATH = "/api/world/none/{world_id}/assets/search"
INDEX_PATH = "/api/world/none/{world_id}/assets/index"
OWNER = "world_semantic_plugin"
logger = logging.getLogger("whiskers.plugins.world_semantic")
SEARCH_FILTERS = ("role", "biome", "licence", "distribution", "pack", "max_tris")


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


def _v3_context(tenant_id: int, world_id: str) -> dict:
    """Server-side pack roots + local capability; never derived from the request."""
    roots = asset_packs.pack_roots_for(tenant_id, world_id)
    return {"pack_roots": roots, "allow_local": "packs-local" in roots}


async def search_assets(world_id: str, query: str | None = None, image: dict | None = None,
                        kind: str | None = None, tags: list[str] | None = None, k: int = 10,
                        role: str | None = None, biome: str | None = None, licence: str | None = None,
                        distribution: str | None = None, pack: str | None = None,
                        max_tris: int | None = None) -> list[dict]:
    """MCP callable: authenticated tenant inferred from bearer; query XOR inline base64 PNG/JPEG."""
    tenant = _authorize(await principal_for_tool(), world_id, "search_assets", "read")
    extra = {name: value for name, value in (("role", role), ("biome", biome), ("licence", licence),
                                             ("distribution", distribution), ("pack", pack),
                                             ("max_tris", max_tris)) if value is not None}
    if extra or asset_packs.pack_roots_for(tenant, world_id):
        extra.update(_v3_context(tenant, world_id))
    return await search_assets_service(query, image=image, tenant_id=tenant, world_id=world_id,
                                       kind=kind, tags=tags, k=k, **extra)


async def index_assets(world_id: str, index: dict) -> dict:
    """MCP callable: stage craft-v3 JSON using only the server-configured trusted ingestion root."""
    tenant = _authorize(await principal_for_tool(), world_id, "index_assets", "write")
    return await _index(index, tenant, world_id)


async def _index(body: dict, tenant: int, world_id: str) -> dict:
    """Version 3 uses configured pack roots; version 1 keeps the single trusted root."""
    if isinstance(body, dict) and body.get("version") == 3:
        return await index_assets_service(body, tenant_id=tenant, world_id=world_id,
                                          pack_roots=asset_packs.pack_roots_for(tenant, world_id))
    return await index_assets_service(body, tenant_id=tenant, world_id=world_id,
                                      trusted_root=_trusted_root(tenant, world_id))


def is_mirrorable(meta) -> bool:
    """Only an explicit `distribution == "shared"` may leave the server; unknown fails closed."""
    return isinstance(meta, dict) and meta.get("distribution", "shared") == "shared"


async def mirror_assets(assets: list[dict], sink, *, tenant_id: int, world_id: str) -> dict:
    """Single choke point for every outbound mirror/sync/export/publish of asset records.

    `distribution=local` (or malformed) assets are skipped and logged, never handed to `sink`.
    """
    mirrored = skipped = 0
    for asset in assets:
        meta = asset.get("metadata") if isinstance(asset, dict) else None
        if not is_mirrorable(meta):
            skipped += 1
            logger.info("asset mirror skipped local asset tenant=%s world=%s asset=%s", tenant_id, world_id,
                        asset.get("asset_id") if isinstance(asset, dict) else None)
            continue
        await sink(asset)
        mirrored += 1
    return {"mirrored": mirrored, "skipped_local": skipped}


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
            result = await _index(body, tenant, world)
            return JSONResponse(result, status_code=202 if result["status"] == "enqueued" else 200)
        if set(body) - {"query", "image", "kind", "tags", "k", *SEARCH_FILTERS}:
            raise ValueError("unknown search parameter")
        if set(body) & set(SEARCH_FILTERS) or asset_packs.pack_roots_for(tenant, world):
            body.update(_v3_context(tenant, world))
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
