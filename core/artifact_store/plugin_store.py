"""Concrete ``IArtifactStore`` adapter — the plugin-facing storage boundary.

Wraps the sync MinIO client (offloaded via ``asyncio.to_thread``, matching what
plugin code previously open-coded itself) and ``db_layer.artifact_link_store``
behind one surface, so plugin code never imports either directly. Mounted on
``PluginContext`` as ``ctx.artifact_store``.
"""
from __future__ import annotations

import asyncio
from typing import Any

import core.artifact_store.minio_client as minio_client
import db_layer.artifact_link_store as artifact_link_store


class ArtifactStore:
    """Concrete ``IArtifactStore`` implementation over MinIO + ``ArtifactLink``."""

    async def put_bytes(
        self,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload ``data`` to ``bucket``/``object_key`` off the event loop."""
        return await asyncio.to_thread(
            minio_client.put_object_bytes, bucket, object_key, data, content_type
        )

    async def get_bytes(self, bucket: str, object_key: str) -> bytes:
        """Download an object's bytes off the event loop."""
        return await asyncio.to_thread(minio_client.get_object_bytes, bucket, object_key)

    async def presigned_url(
        self, bucket: str, object_key: str, expires_seconds: int = 3600
    ) -> str:
        """Return a temporary presigned download URL for an object, off the event loop."""
        return await asyncio.to_thread(
            minio_client.get_presigned_url, bucket, object_key, expires_seconds
        )

    async def create_link(
        self,
        *,
        short_id: str,
        bucket: str,
        object_key: str,
        content_type: str,
        kind: str,
        bytes: int,
        tenant_id: int,
        source_path: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a new short_id -> object mapping."""
        return await artifact_link_store.create_artifact_link(
            short_id=short_id,
            bucket=bucket,
            object_key=object_key,
            content_type=content_type,
            kind=kind,
            bytes=bytes,
            source_path=source_path,
            tenant_id=tenant_id,
            session_id=session_id,
        )

    async def link_by_short_id(self, short_id: str, tenant_id: int) -> dict[str, Any] | None:
        """Fetch one artifact link by short_id, scoped to ``tenant_id``."""
        return await artifact_link_store.get_artifact_link_by_short_id(short_id, tenant_id)

    async def short_id_exists(self, short_id: str) -> bool:
        """Return True when ``short_id`` is already taken."""
        return await artifact_link_store.artifact_link_short_id_exists(short_id)


_instance: ArtifactStore | None = None


def get_artifact_store() -> ArtifactStore:
    """Return the process-wide ``ArtifactStore`` singleton."""
    global _instance
    if _instance is None:
        _instance = ArtifactStore()
    return _instance
