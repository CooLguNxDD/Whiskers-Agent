"""Shared helpers for plugin REST handlers.

Part of the ``api.plugin_routes`` package (split for maintainability).
"""
from __future__ import annotations

import logging
import os

from db_layer.plugin_registry_store import DBPluginRegistry, PluginMetaKey
from core.plugin_loader.skill_file_store import (
    load_skill_from_disk,
    skill_content_hash,
)

logger = logging.getLogger("whiskers")

_db_registry = DBPluginRegistry()

async def _refresh_live_plugin_skills(plugin_id: str) -> None:
    """Rebuild in-memory skill_registry text from DB meta.skills (non-fatal)."""
    try:
        from core.plugin_loader.skill_registry import set_plugin_skills as _set_skills
        fresh = await _db_registry.get_skills(plugin_id)
        text = "\n\n".join(v for v in (fresh or {}).values() if v)
        _set_skills(plugin_id, text)
    except Exception as exc:
        logger.warning("live skill registry refresh for %s failed (non-fatal): %s", plugin_id, exc)


def _skill_row_for(plugin_id: str, key: str, content: str, declared: bool) -> dict:
    """Skill list item with DB/FS content hashes for console validation."""
    content = content or ""
    db_hash = skill_content_hash(content) if content else ""
    disk = load_skill_from_disk(plugin_id, key)
    fs_hash = disk.get("content_hash") if disk.get("ok") else None
    on_disk = bool(disk.get("on_disk"))
    in_sync = bool(db_hash and fs_hash and db_hash == fs_hash)
    return {
        "key": key,
        "content": content,
        "declared": declared,
        "content_hash": db_hash or None,
        "fs_content_hash": fs_hash,
        "on_disk": on_disk,
        "in_sync": in_sync,
    }


def parse_tier(tier_value) -> int:
    """Normalize a tier value to a plain integer."""
    if isinstance(tier_value, int):
        return tier_value
    if isinstance(tier_value, str):
        val = tier_value.lower().strip()
        if val in ("free", "lite"):
            return 1
        if val == "pro":
            return 100
        if val == "admin":
            return 500
        if val == "test":
            return 9999
    return 1


def _build_plugin_payload(plugin, record, oauth_status: dict) -> dict:
    """Merge in-memory plugin instance with DB record into the response shape."""
    description = ""
    required_credentials: list[str] = []
    external_oauth_providers: list[str] = []
    layer2_oauth_enabled = False

    if record is not None:
        description = record.meta.get("description", record.display_name or "")
        required_credentials = record.required_credentials or []
        external_oauth_providers = record.external_oauth_providers or []
        layer2_oauth_enabled = record.meta.get("layer2_oauth_enabled", False)

    tier_raw = getattr(plugin, "tier", 0)
    # Map integer tier to label; fall back to numeric string
    try:
        from core.plugin_loader.plugin_registry import Tier
        tier_label = Tier(tier_raw).name.lower()
    except (ValueError, ImportError):
        tier_label = str(tier_raw)

    plugin_id = getattr(plugin, "name", "")
    auth_status = "ok"
    try:
        from core.plugin_loader.plugin_registry import get_registry, AuthStatus
        auth_status = get_registry().auth.get_auth_status(plugin_id)
    except Exception as exc:
        logger.debug("swallowed exception (non-fatal): %s", exc)

    # Unify Layer 2 OAuth into auth_status: if the plugin uses Layer 2 OAuth,
    # any disconnected external provider means the plugin is not logged in.
    if layer2_oauth_enabled and external_oauth_providers:
        if any(oauth_status.get(p) != "connected" for p in external_oauth_providers):
            try:
                from core.plugin_loader.plugin_registry import AuthStatus
                auth_status = AuthStatus.NEEDS_REAUTH
            except ImportError:
                auth_status = "needs_reauth"

    auth_status_str = auth_status.value if hasattr(auth_status, "value") else str(auth_status)

    content_hash = None
    stale = False
    if record is not None:
        meta = record.meta if isinstance(record.meta, dict) else {}
        # Column-backed (core_037); legacy meta fallback for pre-migration rows.
        content_hash = getattr(record, "content_hash", None) or meta.get("content_hash") or None
        # Staleness only from meta.stale — never from "not loaded" / disabled.
        stale = bool(meta.get(PluginMetaKey.STALE.value))

    return {
        "id": plugin_id,
        "name": plugin_id,
        "version": getattr(plugin, "version", ""),
        "tier": tier_label,
        "enabled": record.is_active if record is not None else True,
        "description": description,
        "required_credentials": required_credentials,
        "external_oauth_providers": external_oauth_providers,
        "oauth_status": oauth_status,
        "capabilities": record.capabilities if record is not None else [],
        "layer2_oauth_enabled": layer2_oauth_enabled,
        "auth_status": auth_status_str,
        "content_hash": content_hash,
        "stale": stale,
    }


