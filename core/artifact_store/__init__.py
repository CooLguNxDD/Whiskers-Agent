"""Core artifact store — MinIO offload, session-gated REST, MCP retrieve tools.

Public surface
--------------
- ``minio_client`` — lazy MinIO singleton, put/get/presign, availability probe
- ``store`` — offload large leaves, short_id links, read/mint helpers
- ``tools`` — MCP ``fetch_artifact`` (primary) + list/get (browse/meta aliases)
- ``routes`` — session-gated REST under ``/api/artifacts/session_gated``
- ``plugin_store`` — ``get_artifact_store()``, the plugin-facing ``IArtifactStore``
  adapter mounted on ``PluginContext.artifact_store``. Plugin code should go
  through this (or ``ctx.artifact_store``), never ``minio_client``/
  ``db_layer.artifact_link_store`` directly.

Import tools/routes for side-effect registration::

    import core.artifact_store.tools   # noqa: F401
    import core.artifact_store.routes  # noqa: F401
"""

from __future__ import annotations

from core.artifact_store.minio_client import (
    DEFAULT_BUCKET,
    ensure_bucket,
    get_minio_client,
    get_object_bytes,
    get_presigned_url,
    minio_available,
    put_object_bytes,
    remove_object,
    reset_client_cache,
)
from core.artifact_store.store import (
    ARTIFACT_BUCKET,
    CONSOLE_PATH_TMPL,
    collect_string_leaves,
    extract_large_artifacts,
    is_offload_marker,
    is_valid_short_id,
    mint_download_url,
    offload_text,
    read_artifact_bytes,
    resolve_artifact_meta,
)
from core.artifact_store.plugin_store import ArtifactStore, get_artifact_store

__all__ = [
    # minio
    "DEFAULT_BUCKET",
    "ensure_bucket",
    "get_minio_client",
    "get_object_bytes",
    "get_presigned_url",
    "minio_available",
    "put_object_bytes",
    "remove_object",
    "reset_client_cache",
    # store
    "ARTIFACT_BUCKET",
    "CONSOLE_PATH_TMPL",
    "collect_string_leaves",
    "extract_large_artifacts",
    "is_offload_marker",
    "is_valid_short_id",
    "mint_download_url",
    "offload_text",
    "read_artifact_bytes",
    "resolve_artifact_meta",
    # plugin-facing adapter
    "ArtifactStore",
    "get_artifact_store",
]
