"""Proxy scope / content-hash / OAuth registration helpers.

Extracted from ``proxy_manager`` so mount lifecycle stays separate from
plugins-row, permission vocabulary, and identity-hash bookkeeping.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.proxy.models import ProxyServer

logger = logging.getLogger("whiskers")


def bump_tool_meta_version() -> None:
    """Invalidate core.context.tool_meta's local tool index after a proxy mount/unmount.

    Fail-open: a missed bump self-heals via that index's short TTL.
    """
    try:
        from core.context.tool_meta import bump_version
        bump_version()
    except Exception:
        logger.debug("tool_meta version bump failed (non-fatal)", exc_info=True)


async def ensure_plugins_row(name: str) -> None:
    """Ensure a plugins table row exists for proxy_{name} to satisfy Vault FK constraints."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from db_layer.connection import get_async_session
    from db_layer.models import PluginModel

    stmt = (
        pg_insert(PluginModel)
        .values(
            id=f"proxy_{name}",
            display_name=f"Proxy: {name}",
            version="1.0.0",
            capabilities=[],
            required_credentials=[],
            external_oauth_providers=[],
            is_active=True,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    async with get_async_session() as session:
        await session.execute(stmt)
        await session.commit()


async def persist_proxy_content_hash(
    name: str,
    url: str,
    transport: str,
    auth_mode: str,
    custom_description: str | None = None,
    workspace_label: str | None = None,
) -> None:
    """Persist identity-tuple content hash on the proxy's plugins row (non-fatal)."""
    try:
        await ensure_plugins_row(name)
        from core.plugin_loader.content_hash import compute_proxy_hash
        from db_layer.plugin_registry_store import DBPluginRegistry

        content_hash = compute_proxy_hash(
            {
                "name": name,
                "url": url,
                "transport": transport,
                "auth_mode": auth_mode,
                "custom_description": custom_description or "",
                "workspace_label": workspace_label or "",
            }
        )
        await DBPluginRegistry().set_content_hash(f"proxy_{name}", content_hash, "1.0.0")
    except Exception as exc:
        logger.warning(
            "ProxyManager: content_hash persist failed for proxy_%s (non-fatal): %s",
            name,
            exc,
        )


async def upsert_oauth_manifest(proxy: "ProxyServer") -> None:
    """Register synthetic OAuth manifest for a proxy row."""
    if proxy.auth_mode != "oauth" or not proxy.oauth_config:
        return
    manifest = {
        "name": f"proxy_{proxy.name}",
        "version": "1.0.0",
        "tier": 1,
        "external_oauth": {
            "upstream": {
                "authorize_url": proxy.oauth_config.get("authorize_url"),
                "token_url": proxy.oauth_config.get("token_url"),
                "client_id": proxy.oauth_config.get("client_id"),
                "scopes": proxy.oauth_config.get("scopes", []),
                "pkce": proxy.oauth_config.get("pkce", "S256"),
                "redirect_path": "/oauth/plugin/upstream/callback",
                "auth_header": proxy.oauth_config.get("auth_header", "Authorization"),
            }
        },
    }
    from core.context import oauth_relay
    if oauth_relay:
        await oauth_relay.upsert_manifest(f"proxy_{proxy.name}", manifest)


# Back-compat private aliases used by ProxyManager / tests.
_bump_tool_meta_version = bump_tool_meta_version
_ensure_plugins_row = ensure_plugins_row
_persist_proxy_content_hash = persist_proxy_content_hash
_upsert_oauth_manifest = upsert_oauth_manifest
