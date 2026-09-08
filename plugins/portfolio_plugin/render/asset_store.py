"""Baked portfolio asset delivery (Phase 6c) -- MinIO storage + public streaming.

Presigned URLs cannot back this: MinIO buckets are private with no anonymous
policy, presigns are generated against ``MINIO_ENDPOINT`` (docker-internal,
unresolvable from a visitor's browser), TTL is 3600s, and baked job layouts
live for months. Every existing artifact route is SESSION_GATED by explicit
design (``core/artifact_store/routes.py``: "No public download surface").

Instead: bytes are stored the same way core.artifact_store does (MinIO +
an ``ArtifactLink`` row for the short_id -> object mapping), but served
through a dedicated PUBLIC route (``routes.py::portfolio_asset``) that
streams the object body directly -- MinIO itself is never exposed, there is
no presign to expire, and the docker-internal hostname never reaches a
browser. The one thing that makes this safe is the ``kind`` allowlist below:
this route must NEVER serve an ``ArtifactLink`` row it didn't create, or it
becomes a public read surface over the entire GOAP offload bucket (the exact
thing the session gate exists to prevent).

Storage access goes through ``core.artifact_store.get_artifact_store()``
(``IArtifactStore``) -- never ``core.artifact_store.minio_client`` or
``db_layer.artifact_link_store`` directly (see ``test_plugin_core_import_boundary.py``).
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from core.artifact_store import get_artifact_store
from plugins.portfolio_plugin.tenant import PORTFOLIO_TENANT_ID

logger = logging.getLogger("whiskers.plugins.portfolio")

ASSET_BUCKET = "portfolio-assets"
# The only ArtifactLink.kind values portfolio_asset (the public route) will
# ever serve. Deliberately narrow -- see module docstring.
PORTFOLIO_ASSET_KINDS = frozenset({"portfolio_asset"})
_SHORT_ID_RE = re.compile(r"^[a-z0-9_]{1,80}$")
_MAX_SHORT_ID_ATTEMPTS = 5


def is_valid_asset_short_id(short_id: str) -> bool:
    """Return True if the short_id matches the allowed asset identifier format."""
    return bool(short_id) and bool(_SHORT_ID_RE.match(short_id))


async def store_png_asset(
    png_bytes: bytes,
    *,
    tenant_id: int = PORTFOLIO_TENANT_ID,
    source_path: str | None = None,
) -> dict[str, Any] | None:
    """Upload PNG bytes to MinIO and mint a portfolio_asset ArtifactLink.
    Fails open (returns None, never raises) -- callers should treat a miss
    as "no baked asset available" and fall back to inline SVG."""
    if not png_bytes:
        return None
    try:
        from utils.short_id import generate_short_id

        store = get_artifact_store()
        sha8 = hashlib.sha256(png_bytes).hexdigest()[:8]
        object_key = f"{int(tenant_id)}/portfolio_asset-{sha8}.png"

        # Resolve short_id before uploading: give-up-on-collision must not
        # leave an orphaned object in MinIO.
        short_id = None
        for _ in range(_MAX_SHORT_ID_ATTEMPTS):
            candidate = generate_short_id(["asset", "portfolio"])
            if not await store.short_id_exists(candidate):
                short_id = candidate
                break
        if short_id is None:
            logger.warning("store_png_asset: short_id collision, giving up")
            return None

        await store.put_bytes(ASSET_BUCKET, object_key, png_bytes, "image/png")

        return await store.create_link(
            short_id=short_id,
            bucket=ASSET_BUCKET,
            object_key=object_key,
            content_type="image/png",
            kind="portfolio_asset",
            bytes=len(png_bytes),
            source_path=source_path,
            tenant_id=int(tenant_id),
            session_id=None,
        )
    except Exception:
        logger.warning("store_png_asset: upload failed", exc_info=True)
        return None


async def resolve_public_asset(
    short_id: str, *, tenant_id: int = PORTFOLIO_TENANT_ID
) -> tuple[dict[str, Any], bytes] | None:
    """Look up + read a portfolio asset for the public route.

    **Single-tenant by design:** ``tenant_id`` defaults to
    ``PORTFOLIO_TENANT_ID`` (1). Public HR-facing assets are not multi-tenant;
    the hard safety gate is ``kind in PORTFOLIO_ASSET_KINDS`` (never serve a
    GOAP offload link). Multi-tenant portfolio would need a design pass
    before changing this default.

    Returns None on: invalid short_id, not found, wrong ``kind`` (the
    allowlist gate), or a MinIO read failure. Never raises.
    """
    if not is_valid_asset_short_id(short_id):
        return None
    store = get_artifact_store()
    try:
        meta = await store.link_by_short_id(short_id, tenant_id)
    except Exception:
        logger.warning("resolve_public_asset: lookup failed short_id=%s", short_id, exc_info=True)
        return None
    if not meta:
        return None
    if meta.get("kind") not in PORTFOLIO_ASSET_KINDS:
        logger.warning(
            "resolve_public_asset: refused non-portfolio_asset kind=%r short_id=%s",
            meta.get("kind"), short_id,
        )
        return None
    try:
        body = await store.get_bytes(meta["bucket"], meta["object_key"])
    except Exception:
        logger.warning("resolve_public_asset: read failed short_id=%s", short_id, exc_info=True)
        return None
    return meta, body
