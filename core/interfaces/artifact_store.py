"""Protocol interface for plugin-facing artifact/object storage.

Covers both object bytes (MinIO) and the ``ArtifactLink`` short_id mapping —
a storage accessor limited to bytes alone still leaves plugins reaching
``db_layer.artifact_link_store`` directly for link minting/lookup, which is
exactly the leak this interface exists to close (see
``plugins/portfolio_plugin/render/asset_store.py``'s pre-refactor imports).
"""

from typing import Any, Protocol


class IArtifactStore(Protocol):
    """Protocol defining the plugin-facing artifact storage interface."""

    async def put_bytes(
        self,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload ``data`` to ``bucket``/``object_key``, returning the object key."""
        ...

    async def get_bytes(self, bucket: str, object_key: str) -> bytes:
        """Download an object's bytes from ``bucket``/``object_key``."""
        ...

    async def presigned_url(
        self, bucket: str, object_key: str, expires_seconds: int = 3600
    ) -> str:
        """Return a temporary presigned download URL for an object."""
        ...

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
        """Persist a new short_id -> object mapping (``ArtifactLink`` row)."""
        ...

    async def link_by_short_id(self, short_id: str, tenant_id: int) -> dict[str, Any] | None:
        """Fetch one artifact link by short_id, scoped to ``tenant_id``."""
        ...

    async def short_id_exists(self, short_id: str) -> bool:
        """Return True when ``short_id`` is already taken (global uniqueness)."""
        ...
