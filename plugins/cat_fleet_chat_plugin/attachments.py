"""Fleet attachment bytes in MinIO, behind the core artifact-store boundary.

The hub stores descriptors only (bucket, object key, size, type). This module
uploads and reads the bytes through ``core.artifact_store.get_artifact_store()``
and never imports the MinIO client directly (plugin import boundary).

Every read is pinned to the manifest's ``attachment_bucket``. Hub descriptors
are user-supplied data, so without the pin a crafted message could make this
plugin presign objects in another bucket (for example job-search resumes).
The bucket is not in the ``artifact_sweep`` allowlist, so fleet files are not
deleted by the ephemeral-artifact retention window.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import re
import uuid
from typing import Any

from core.artifact_store import get_artifact_store

from plugins.cat_fleet_chat_plugin.plugin_config import SETTINGS

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_TEXT_TYPES = ("application/json", "application/xml", "application/x-yaml", "application/yaml")
_EXTRA_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


class AttachmentError(ValueError):
    """Bad input or a descriptor this plugin refuses to read. ``code`` is the tool error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _setting_int(name: str, default: int) -> int:
    try:
        return int(SETTINGS.get(name, default))
    except (TypeError, ValueError):
        return default


def bucket() -> str:
    """The one bucket this plugin writes and reads."""
    return str(SETTINGS.get("attachment_bucket") or "cat-fleet-attachments")


def max_bytes() -> int:
    return _setting_int("attachment_max_bytes", 10 * 1024 * 1024)


def inline_max_bytes() -> int:
    return _setting_int("attachment_inline_max_bytes", 256 * 1024)


def url_ttl_seconds() -> int:
    return max(60, _setting_int("attachment_url_ttl_s", 3600))


def safe_filename(name: str) -> str:
    """Last path segment, reduced to a conservative object-key alphabet."""
    base = re.split(r"[\\/]", name.strip())[-1]
    cleaned = _UNSAFE.sub("_", base).strip("._") or "file"
    return cleaned[:200]


def decode_content(content_text: str | None, content_base64: str | None) -> bytes:
    """Exactly one of text (UTF-8) or base64 must be given."""
    if (content_text is None) == (content_base64 is None):
        raise AttachmentError(
            "validation_error", "pass exactly one of content_text or content_base64"
        )
    if content_text is not None:
        return content_text.encode("utf-8")
    try:
        return base64.b64decode(content_base64 or "", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttachmentError("validation_error", "content_base64 is not valid base64") from exc


def is_text(content_type: str) -> bool:
    return content_type.startswith("text/") or content_type in _TEXT_TYPES


async def upload(
    channel: str,
    filename: str,
    data: bytes,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Store ``data`` and return the hub attachment descriptor."""
    limit = max_bytes()
    if len(data) > limit:
        raise AttachmentError(
            "attachment_too_large", f"attachment is {len(data)} bytes; the limit is {limit}"
        )
    name = safe_filename(filename)
    extension = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    media = (
        content_type
        or mimetypes.guess_type(name)[0]
        or _EXTRA_TYPES.get(extension)
        or "application/octet-stream"
    )
    key = f"fleet/{channel}/{uuid.uuid4().hex}/{name}"
    target = bucket()
    await get_artifact_store().put_bytes(target, key, data, media)
    return {
        "filename": name,
        "content_type": media,
        "size_bytes": len(data),
        "storage": "minio",
        "bucket": target,
        "object_key": key,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def check_owned(descriptor: dict[str, Any]) -> None:
    """Refuse any descriptor outside this plugin's bucket and key prefix."""
    key = str(descriptor.get("object_key") or "")
    if (
        descriptor.get("storage") != "minio"
        or descriptor.get("bucket") != bucket()
        or not key.startswith("fleet/")
        or ".." in key.split("/")
    ):
        raise AttachmentError(
            "forbidden_attachment",
            "attachment is not stored in this plugin's bucket; refusing to read it",
        )


async def describe(descriptor: dict[str, Any], include_content: bool) -> dict[str, Any]:
    """Descriptor plus a presigned URL, and the bytes when asked and small enough."""
    check_owned(descriptor)
    store = get_artifact_store()
    ttl = url_ttl_seconds()
    result = dict(descriptor)
    result["presigned_url"] = await store.presigned_url(
        descriptor["bucket"], descriptor["object_key"], ttl
    )
    result["presigned_url_expires_s"] = ttl
    if not include_content:
        return result
    size = int(descriptor.get("size_bytes") or 0)
    cap = inline_max_bytes()
    if size > cap:
        result["content_omitted"] = f"{size} bytes exceeds the {cap}-byte inline limit; use presigned_url"
        return result
    data = await store.get_bytes(descriptor["bucket"], descriptor["object_key"])
    if len(data) > cap:
        result["content_omitted"] = f"{len(data)} bytes exceeds the {cap}-byte inline limit; use presigned_url"
        return result
    if is_text(str(descriptor.get("content_type") or "")):
        result["content_text"] = data.decode("utf-8", errors="replace")
    else:
        result["content_base64"] = base64.b64encode(data).decode("ascii")
    return result
