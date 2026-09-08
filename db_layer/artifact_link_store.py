"""CRUD for artifact_links — short-id → MinIO object mapping (tenant-scoped)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, delete

from db_layer.connection import get_async_session
from db_layer.models.artifacts import ArtifactLink

logger = logging.getLogger("whiskers")


def _row_to_dict(row: ArtifactLink) -> dict[str, Any]:
    """Serialize an ArtifactLink ORM row to a plain dict."""
    return {
        "id": row.id,
        "short_id": row.short_id,
        "bucket": row.bucket,
        "object_key": row.object_key,
        "content_type": row.content_type or "text/markdown",
        "kind": row.kind or "blob",
        "bytes": int(row.bytes or 0),
        "source_path": row.source_path,
        "tenant_id": int(row.tenant_id) if row.tenant_id is not None else None,
        "session_id": row.session_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def artifact_link_short_id_exists(short_id: str) -> bool:
    """Return True when short_id is already taken (global uniqueness)."""
    async with get_async_session() as session:
        stmt = select(ArtifactLink.id).where(ArtifactLink.short_id == short_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def create_artifact_link(
    *,
    short_id: str,
    bucket: str,
    object_key: str,
    content_type: str = "text/markdown",
    kind: str = "blob",
    bytes: int = 0,
    source_path: str | None = None,
    tenant_id: int,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Persist a new artifact link. tenant_id is required (no cross-tenant default)."""
    if tenant_id is None:
        raise ValueError("tenant_id is required for create_artifact_link")
    async with get_async_session() as session:
        row = ArtifactLink(
            short_id=short_id,
            bucket=bucket,
            object_key=object_key,
            content_type=content_type,
            kind=kind,
            bytes=int(bytes or 0),
            source_path=source_path,
            tenant_id=int(tenant_id),
            session_id=session_id,
        )
        session.add(row)
        await session.flush()
        out = _row_to_dict(row)
        await session.commit()
        return out


async def get_artifact_link_by_short_id(
    short_id: str,
    tenant_id: int,
) -> dict[str, Any] | None:
    """Fetch one artifact by short_id, strictly scoped to tenant_id."""
    if tenant_id is None:
        raise ValueError("tenant_id is required for get_artifact_link_by_short_id")
    async with get_async_session() as session:
        stmt = select(ArtifactLink).where(
            ArtifactLink.short_id == short_id,
            ArtifactLink.tenant_id == int(tenant_id),
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if not row:
            return None
        return _row_to_dict(row)


async def list_artifact_links(
    tenant_id: int,
    *,
    session_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List recent artifacts for a tenant; optional session_id filter."""
    if tenant_id is None:
        raise ValueError("tenant_id is required for list_artifact_links")
    limit = max(1, min(int(limit or 50), 200))
    async with get_async_session() as session:
        stmt = (
            select(ArtifactLink)
            .where(ArtifactLink.tenant_id == int(tenant_id))
            .order_by(ArtifactLink.created_at.desc())
            .limit(limit)
        )
        if session_id:
            stmt = stmt.where(ArtifactLink.session_id == session_id)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [_row_to_dict(r) for r in rows]


async def delete_artifact_link(short_id: str, tenant_id: int) -> bool:
    """Delete an artifact row for the given tenant. Returns True if a row was removed."""
    if tenant_id is None:
        raise ValueError("tenant_id is required for delete_artifact_link")
    async with get_async_session() as session:
        stmt = delete(ArtifactLink).where(
            ArtifactLink.short_id == short_id,
            ArtifactLink.tenant_id == int(tenant_id),
        )
        result = await session.execute(stmt)
        await session.commit()
        return (result.rowcount or 0) > 0
