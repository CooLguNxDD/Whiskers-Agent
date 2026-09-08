"""MCP tools for retrieving GOAP-offloaded MinIO artifacts.

Native agent path companion to session-gated REST routes in ``routes.py``.
GOAP-denylisted so the planner does not pick them as random steps; hosts and
CLI agents call them with a short_id from an offload marker.

``fetch_artifact`` is the primary deep-read tool (meta + content). Prefer the
inline preview on the parent tool result; call this only when the full body
is required. ``get_artifact`` is a thin meta-only alias kept for back-compat.

Part of the ``core.artifact_store`` feature package.
"""

from __future__ import annotations

import logging
from typing import Any

from core.context import mcp, current_tenant_id
from core.artifact_store.store import (
    is_valid_short_id,
    mint_download_url,
    read_artifact_bytes,
    resolve_artifact_meta,
)

logger = logging.getLogger("whiskers")

_TAGS = {"artifacts", "core_graph"}


def _tenant() -> int:
    try:
        return int(current_tenant_id.get() or 1)
    except Exception as exc:
        logger.warning("Failed to resolve tenant_id, defaulting to 1: %s", exc, exc_info=True)
        return 1


def _public_meta(row: dict[str, Any]) -> dict[str, Any]:
    """Strip internal MinIO keys from tool payloads."""
    return {
        "short_id": row.get("short_id"),
        "kind": row.get("kind"),
        "bytes": row.get("bytes"),
        "content_type": row.get("content_type"),
        "session_id": row.get("session_id"),
        "source_path": row.get("source_path") or row.get("path"),
        "created_at": row.get("created_at"),
        "console_path": f"/api/artifacts/session_gated/{row.get('short_id')}",
    }


@mcp.tool(
    name="list_artifacts",
    tags=_TAGS,
    annotations={"readOnlyHint": True},
)
async def list_artifacts(
    session_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Browse offloaded artifacts for a session (not a normal step after every Jules call).

    Prefer the inline preview on the parent tool result. Use this only to recover
    short_ids when the parent marker was lost. For content, call fetch_artifact.

    Args:
        session_id: Optional graph/chat session filter.
        limit: Max rows (1–200, default 50).
    """
    from db_layer.artifact_link_store import list_artifact_links

    tenant_id = _tenant()
    try:
        rows = await list_artifact_links(
            tenant_id, session_id=session_id, limit=limit
        )
    except Exception as exc:
        logger.exception("list_artifacts failed: %s", exc)
        return {"status": "error", "message": str(exc)}
    artifacts = [_public_meta(r) for r in rows]
    return {"status": "ok", "count": len(artifacts), "artifacts": artifacts}


async def _load_artifact(
    short_id: str,
    *,
    include_content: bool = True,
    include_url: bool = False,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Shared loader for fetch_artifact / get_artifact."""
    if not is_valid_short_id(short_id or ""):
        return {"status": "error", "message": "invalid_short_id"}

    tenant_id = _tenant()

    if not include_content:
        meta = await resolve_artifact_meta(short_id, tenant_id)
        if not meta:
            return {"status": "error", "message": "not_found"}
        out = {"status": "ok", **_public_meta(meta)}
        if include_url:
            from utils.server_config import ARTIFACT_OFFLOAD_URL_EXPIRY_SECONDS

            url = await mint_download_url(
                short_id, tenant_id, expires_s=ARTIFACT_OFFLOAD_URL_EXPIRY_SECONDS
            )
            if url:
                out["download_url"] = url
        return out

    from utils.server_config import ARTIFACT_OFFLOAD_FETCH_DEFAULT_MAX_CHARS

    cap = (
        int(max_chars)
        if max_chars is not None
        else int(ARTIFACT_OFFLOAD_FETCH_DEFAULT_MAX_CHARS)
    )
    cap = max(1, min(cap, 500_000))

    loaded = await read_artifact_bytes(short_id, tenant_id)
    if not loaded:
        return {"status": "error", "message": "not_found"}
    meta, body = loaded
    try:
        text = body.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        text = body.decode("latin-1", errors="replace")
    truncated = len(text) > cap
    if truncated:
        text = text[:cap]
    out: dict[str, Any] = {
        "status": "ok",
        **_public_meta(meta),
        "bytes": meta.get("bytes"),
        "content": text,
        "truncated": truncated,
        "max_chars": cap,
    }
    if include_url:
        from utils.server_config import ARTIFACT_OFFLOAD_URL_EXPIRY_SECONDS

        url = await mint_download_url(
            short_id, tenant_id, expires_s=ARTIFACT_OFFLOAD_URL_EXPIRY_SECONDS
        )
        if url:
            out["download_url"] = url
    return out


@mcp.tool(
    name="get_artifact",
    tags=_TAGS,
    annotations={"readOnlyHint": True},
)
async def get_artifact(
    short_id: str,
    include_url: bool = False,
) -> dict[str, Any]:
    """Metadata only for one artifact (thin alias of fetch_artifact).

    Prefer fetch_artifact when you need content. Prefer the inline preview on
    the parent tool result for summary decisions — skip this tool unless you
    only need short_id/kind/bytes.

    Args:
        short_id: Artifact short id from an offload marker.
        include_url: When true, include a short-lived MinIO presigned download_url.
    """
    return await _load_artifact(
        short_id, include_content=False, include_url=include_url
    )


@mcp.tool(
    name="fetch_artifact",
    tags=_TAGS,
    annotations={"readOnlyHint": True},
)
async def fetch_artifact(
    short_id: str,
    max_chars: int | None = None,
    include_content: bool = True,
    include_url: bool = False,
) -> dict[str, Any]:
    """Deep-read an offloaded body (meta + content). Not a normal follow-up to every tool call.

    Prefer the inline preview on the parent tool result (Jules list/get, etc.).
    Call this ONLY when the preview is insufficient — e.g. you must quote or
    apply a full diff/log. With include_content=false returns meta only
    (same as get_artifact).

    Args:
        short_id: Artifact short id from an offload marker (art_…).
        max_chars: Cap on returned content length (default from config, ~100k).
        include_content: When false, return metadata only (no MinIO body read).
        include_url: When true, include a short-lived MinIO presigned download_url.
    """
    return await _load_artifact(
        short_id,
        include_content=include_content,
        include_url=include_url,
        max_chars=max_chars,
    )
