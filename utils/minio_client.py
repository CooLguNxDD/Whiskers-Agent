"""Back-compat shim — MinIO client lives in ``core.artifact_store.minio_client``."""

from core.artifact_store.minio_client import (  # noqa: F401
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

__all__ = [
    "DEFAULT_BUCKET",
    "ensure_bucket",
    "get_minio_client",
    "get_object_bytes",
    "get_presigned_url",
    "minio_available",
    "put_object_bytes",
    "remove_object",
    "reset_client_cache",
]
