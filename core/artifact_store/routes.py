"""
Session-gated artifact REST API (GOAP MinIO offload).

Endpoints
---------
GET    /api/artifacts/session_gated                      — List recent artifacts (tenant-scoped)
GET    /api/artifacts/session_gated/{short_id}            — Artifact metadata
GET    /api/artifacts/session_gated/{short_id}/download   — 302 to fresh presigned MinIO URL
DELETE /api/artifacts/session_gated/{short_id}            — Delete DB row (+ best-effort MinIO object)

No public (AuthPolicy.PUBLIC) download surface.

Part of the ``core.artifact_store`` feature package.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from core.context import http_route_registry, current_tenant_id
from core.http_route_registry import AuthPolicy
from core.artifact_store.store import is_valid_short_id, mint_download_url

logger = logging.getLogger("whiskers")


def _tenant_id() -> int:
    """Resolve tenant from request context (default 1)."""
    try:
        return int(current_tenant_id.get() or 1)
    except Exception as exc:
        logger.warning("Failed to resolve tenant_id, defaulting to 1: %s", exc, exc_info=True)
        return 1


@http_route_registry.route(
    route="artifacts",
    endpoint="",
    methods=["GET"],
    name="api_artifacts_list",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.artifacts",
)
async def api_artifacts_list(request: Request) -> Response:
    """List recent artifacts for the session principal's tenant."""
    from db_layer.artifact_link_store import list_artifact_links

    tenant_id = _tenant_id()
    session_id = request.query_params.get("session_id") or None
    try:
        limit = int(request.query_params.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    try:
        rows = await list_artifact_links(
            tenant_id, session_id=session_id, limit=limit
        )
    except Exception as exc:
        logger.exception("list_artifact_links failed: %s", exc)
        return JSONResponse({"error": "list_failed"}, status_code=503)
    # Drop internal object_key from list payloads (download uses meta lookup).
    artifacts = [
        {
            "short_id": r["short_id"],
            "kind": r["kind"],
            "bytes": r["bytes"],
            "content_type": r["content_type"],
            "session_id": r["session_id"],
            "source_path": r["source_path"],
            "created_at": r["created_at"],
            "console_path": f"/api/artifacts/session_gated/{r['short_id']}",
        }
        for r in rows
    ]
    return JSONResponse(
        {"status": "ok", "count": len(artifacts), "artifacts": artifacts},
        headers={"Cache-Control": "private, no-store"},
    )


@http_route_registry.route(
    route="artifacts",
    endpoint="{short_id}",
    methods=["GET"],
    name="api_artifacts_get",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.artifacts",
)
async def api_artifacts_get(request: Request) -> Response:
    """Return metadata for one artifact (tenant-scoped)."""
    from db_layer.artifact_link_store import get_artifact_link_by_short_id

    short_id = (request.path_params.get("short_id") or "").strip()
    if not is_valid_short_id(short_id):
        return JSONResponse({"error": "invalid_short_id"}, status_code=400)
    tenant_id = _tenant_id()
    try:
        row = await get_artifact_link_by_short_id(short_id, tenant_id)
    except Exception as exc:
        logger.exception("get_artifact_link failed: %s", exc)
        return JSONResponse({"error": "lookup_failed"}, status_code=503)
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    body: dict[str, Any] = {
        "status": "ok",
        "short_id": row["short_id"],
        "kind": row["kind"],
        "bytes": row["bytes"],
        "content_type": row["content_type"],
        "session_id": row["session_id"],
        "source_path": row["source_path"],
        "created_at": row["created_at"],
        "console_path": f"/api/artifacts/session_gated/{row['short_id']}",
    }
    return JSONResponse(body, headers={"Cache-Control": "private, no-store"})


@http_route_registry.route(
    route="artifacts",
    endpoint="{short_id}/download",
    methods=["GET"],
    name="api_artifacts_download",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.artifacts",
)
async def api_artifacts_download(request: Request) -> Response:
    """302-redirect to a freshly minted MinIO presigned URL (session-gated)."""
    from utils.server_config import ARTIFACT_OFFLOAD_URL_EXPIRY_SECONDS

    short_id = (request.path_params.get("short_id") or "").strip()
    if not is_valid_short_id(short_id):
        return JSONResponse({"error": "invalid_short_id"}, status_code=400)
    tenant_id = _tenant_id()
    url = await mint_download_url(
        short_id, tenant_id, expires_s=ARTIFACT_OFFLOAD_URL_EXPIRY_SECONDS
    )
    if not url:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return RedirectResponse(
        url,
        status_code=302,
        headers={"Cache-Control": "private, no-store"},
    )


@http_route_registry.route(
    route="artifacts",
    endpoint="{short_id}",
    methods=["DELETE"],
    name="api_artifacts_delete",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.artifacts",
)
async def api_artifacts_delete(request: Request) -> Response:
    """Delete artifact DB row; best-effort MinIO object removal."""
    from db_layer.artifact_link_store import (
        delete_artifact_link,
        get_artifact_link_by_short_id,
    )

    short_id = (request.path_params.get("short_id") or "").strip()
    if not is_valid_short_id(short_id):
        return JSONResponse({"error": "invalid_short_id"}, status_code=400)
    tenant_id = _tenant_id()
    row = await get_artifact_link_by_short_id(short_id, tenant_id)
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)

    # Best-effort object delete — DB row is source of truth for access control.
    try:
        from core.artifact_store.minio_client import remove_object

        await asyncio.to_thread(remove_object, row["bucket"], row["object_key"])
    except Exception as exc:
        logger.warning("MinIO remove_object failed short_id=%s: %s", short_id, exc, exc_info=True)

    deleted = await delete_artifact_link(short_id, tenant_id)
    if not deleted:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(
        {"status": "ok", "short_id": short_id, "deleted": True},
        headers={"Cache-Control": "private, no-store"},
    )
