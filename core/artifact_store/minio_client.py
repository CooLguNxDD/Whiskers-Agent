"""MinIO client — lazy singleton for object storage (artifacts, resume PDFs, etc.)."""

from __future__ import annotations

import io
import logging
import os
from datetime import timedelta

from minio import Minio

logger = logging.getLogger("whiskers")

# Global singleton client instance cache
_minio_client = None
# Cached availability probe: None = not probed yet
_minio_available_cache: bool | None = None

DEFAULT_BUCKET = os.environ.get("MINIO_BUCKET", "job-search-resumes")


def minio_available(*, force_refresh: bool = False) -> bool:
    """Cheap probe: MinIO env is set and the client can reach the endpoint.

    Cached after first success/failure. Used by response_shape offload_minio
    default so strip/CSV still runs when object storage is down.
    """
    global _minio_available_cache
    if not force_refresh and _minio_available_cache is not None:
        return _minio_available_cache

    access = (os.environ.get("MINIO_ACCESS_KEY") or "").strip()
    secret = (os.environ.get("MINIO_SECRET_KEY") or "").strip()
    if not access or not secret:
        _minio_available_cache = False
        return False

    try:
        client = get_minio_client()
        # list_buckets is a light authenticated round-trip
        client.list_buckets()
        _minio_available_cache = True
    except Exception as exc:
        logger.debug("minio_available probe failed: %s", exc)
        _minio_available_cache = False
    return bool(_minio_available_cache)


def get_minio_client() -> Minio:
    """Get or construct a lazy singleton MinIO client instance.

    Reads MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, and MINIO_SECURE from the environment.
    """
    global _minio_client
    if _minio_client is None:
        endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000")
        access_key = os.environ.get("MINIO_ACCESS_KEY", "")
        secret_key = os.environ.get("MINIO_SECRET_KEY", "")
        secure_val = os.environ.get("MINIO_SECURE", "false")
        # Determine secure boolean connection setting
        secure = secure_val.lower() == "true"

        logger.info(f"Initializing MinIO client with endpoint {endpoint} (secure={secure})")
        _minio_client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
    return _minio_client


def ensure_bucket(bucket: str) -> None:
    """Check if a bucket exists in MinIO, and create it if it does not.

    Idempotent helper to verify bucket readiness.
    """
    client = get_minio_client()
    # Check if the bucket already exists before trying to create it
    if not client.bucket_exists(bucket):
        logger.info(f"Bucket {bucket} does not exist. Creating it.")
        client.make_bucket(bucket)


def put_object_bytes(
    bucket: str,
    object_key: str,
    data: bytes,
    content_type: str = "application/pdf",
) -> str:
    """Upload data as bytes to a specified bucket and object key.

    Ensures the bucket exists first, then uploads using client.put_object.
    """
    ensure_bucket(bucket)
    client = get_minio_client()
    data_stream = io.BytesIO(data)
    # Stream the bytes payload into the MinIO bucket
    client.put_object(
        bucket,
        object_key,
        data_stream,
        length=len(data),
        content_type=content_type,
    )
    return object_key


def get_presigned_url(bucket: str, object_key: str, expires_seconds: int = 3600) -> str:
    """Generate a temporary presigned URL for downloading a specific object.

    Delegates to MinIO's presigned_get_object with a timedelta expiration.
    """
    client = get_minio_client()
    expires_delta = timedelta(seconds=expires_seconds)
    # Generate the signed download link for clients/users
    return client.presigned_get_object(bucket, object_key, expires=expires_delta)


def get_object_bytes(bucket: str, object_key: str) -> bytes:
    """Download an object body as bytes from MinIO."""
    client = get_minio_client()
    response = client.get_object(bucket, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def remove_object(bucket: str, object_key: str) -> None:
    """Delete an object from MinIO (best-effort callers wrap in try/except)."""
    client = get_minio_client()
    client.remove_object(bucket, object_key)


def reset_client_cache() -> None:
    """Clear the cached MinIO client singleton instance.

    Mainly used as a test helper to force client re-initialization.
    """
    global _minio_client, _minio_available_cache
    _minio_client = None
    _minio_available_cache = None
