"""
Live operation catalog REST API (inference contract layer).

Backed by in-memory OperationCatalog — not route_embeddings.

Endpoints
---------
GET    /api/catalog/session_gated              — Entitlement-filtered ops + revision/ETag
GET    /api/catalog/session_gated/openapi      — OpenAPI 3.0 for HTTP-exposed ops (filtered)
POST   /api/catalog/session_gated/execute      — Validate args and invoke (plugin_id, operation_id)
"""

from __future__ import annotations

import hashlib
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from core.route_registry.operation_catalog import get_operation_catalog
from core.route_registry.operation_descriptor import OperationDescriptor

logger = logging.getLogger("whiskers_agent")


async def _resolve_caller_scopes(request: Request) -> list[str] | None:
    """Resolve catalog/execute scopes from the session cookie principal.

    Returns None only when no session role can be resolved (middleware should
    already have gated SESSION_GATED routes). Admin bypass roles get full access.
    """
    session_token = request.cookies.get("session")
    if not session_token:
        return []
    try:
        from core.context import oauth_provider
        if oauth_provider is None or oauth_provider._svc is None:
            return []
        payload = await oauth_provider._svc.validate_token(session_token)
    except Exception:
        logger.debug("catalog: session token validate failed", exc_info=True)
        return []

    role = (payload.get("whiskers_role") or payload.get("ocat_role")) if isinstance(payload, dict) else None
    from core.api_key_management.scopes import playground_mcp_scopes
    return playground_mcp_scopes(role)


def _match_etag(request: Request, etag: str) -> bool:
    """True when If-None-Match matches the current catalog ETag."""
    inm = request.headers.get("if-none-match") or request.headers.get("If-None-Match")
    if not inm:
        return False
    # Allow weak/strong and comma-separated lists
    candidates = [p.strip() for p in inm.split(",")]
    return etag in candidates or etag.strip('"') in {c.strip('"') for c in candidates}


def _view_etag(
    kind: str,
    revision: int,
    ops: list[OperationDescriptor],
    plugin_id: str | None,
    slot: str | None = None,
) -> str:
    """ETag for one filtered catalog representation, not the global catalog hash.

    Scopes, ``plugin_id``, and ``slot`` change which ops are returned; a 304
    decided from ``catalog.etag`` before those filters would collapse distinct
    views into one validator.
    """
    parts = [kind, str(revision), plugin_id or "", slot or ""]
    parts.extend(sorted(op.descriptor_hash for op in ops))
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
    return f'"{digest}"'


def _not_modified(etag: str) -> Response:
    """Empty 304 that still forbids storing a body (same as the 200 path)."""
    return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-store"})


async def _filtered_catalog_ops(
    request: Request,
    *,
    apply_slot: bool,
) -> tuple[list[OperationDescriptor], str | None, str | None, int]:
    """Scope- and query-filter the live catalog, then return (ops, plugin_id, slot, revision)."""
    catalog = get_operation_catalog()
    caller_scopes = await _resolve_caller_scopes(request)
    ops = catalog.filter_for_caller(caller_scopes)
    plugin_id = request.query_params.get("plugin_id")
    slot = request.query_params.get("slot") if apply_slot else None
    if plugin_id:
        ops = [o for o in ops if o.plugin_id == plugin_id]
    if slot:
        ops = [o for o in ops if o.ui is not None and o.ui.slot == slot]
    return ops, plugin_id, slot, catalog.revision


@http_route_registry.route(
    route="catalog",
    endpoint="",
    methods=["GET"],
    name="api_catalog_list",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.catalog",
)
async def api_catalog_list(request: Request) -> Response:
    """Return versioned, entitlement-filtered live operation catalog."""
    ops, plugin_id, slot, revision = await _filtered_catalog_ops(request, apply_slot=True)
    etag = _view_etag("list", revision, ops, plugin_id, slot)
    if _match_etag(request, etag):
        return _not_modified(etag)

    body = {
        "revision": revision,
        "etag": etag,
        "operations": [o.to_catalog_dict() for o in ops],
    }
    return JSONResponse(
        body,
        headers={
            "ETag": etag,
            "Cache-Control": "no-store",
        },
    )


@http_route_registry.route(
    route="catalog",
    endpoint="openapi",
    methods=["GET"],
    name="api_catalog_openapi",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.catalog",
)
async def api_catalog_openapi(request: Request) -> Response:
    """Return entitlement-filtered OpenAPI 3.0 doc for HTTP-exposed catalog ops."""
    ops, plugin_id, _slot, revision = await _filtered_catalog_ops(request, apply_slot=False)
    etag = _view_etag("openapi", revision, ops, plugin_id)
    if _match_etag(request, etag):
        return _not_modified(etag)

    from core.route_registry.openapi_export import build_openapi_from_ops

    doc = build_openapi_from_ops(ops)
    return JSONResponse(
        doc,
        headers={
            "ETag": etag,
            "Cache-Control": "no-store",
        },
    )


@http_route_registry.route(
    route="catalog",
    endpoint="execute",
    methods=["POST"],
    name="api_catalog_execute",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.catalog",
)
async def api_catalog_execute(request: Request) -> Response:
    """Execute an operation by (plugin_id, operation_id) with server-side schema validation."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    plugin_id = body.get("plugin_id")
    operation_id = body.get("operation_id")
    args = body.get("args") if "args" in body else body.get("arguments", {})
    if not plugin_id or not isinstance(plugin_id, str):
        return JSONResponse(
            {"error": "plugin_id_required", "message": "Execution requires plugin_id + operation_id"},
            status_code=400,
        )
    if not operation_id or not isinstance(operation_id, str):
        return JSONResponse(
            {"error": "operation_id_required", "message": "Execution requires plugin_id + operation_id"},
            status_code=400,
        )
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return JSONResponse({"error": "invalid_args", "message": "args must be an object"}, status_code=400)

    caller_scopes = await _resolve_caller_scopes(request)
    from core.route_registry.execute import execute_operation, ExecuteError

    try:
        result = await execute_operation(
            plugin_id,
            operation_id,
            args,
            caller_scopes=caller_scopes,
        )
    except ExecuteError as exc:
        return JSONResponse(
            {"error": exc.code, "message": exc.message, "details": exc.details},
            status_code=exc.status,
        )
    except Exception:
        logger.exception("catalog execute failed plugin=%s op=%s", plugin_id, operation_id)
        return JSONResponse({"error": "execute_failed"}, status_code=500)

    return JSONResponse({"status": "ok", "result": result})
