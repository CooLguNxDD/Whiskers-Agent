"""Backward-compat shim over core.scope_management.registration.

Vocabulary source of truth moved to ``core.scope_management.registration``
(``PermissionRegistry`` — the ScopeManager's front door for plugin/proxy
permission registration). This module is kept so existing importers
(plugin_loader, plugin_lifecycle_registry, proxy_manager, api_key_routes,
tests) and any monkeypatching by function name keep working unchanged.
"""

from typing import Optional

from core.scope_management.registration import (
    get_permission_registry as _get_registry,
    normalize_scope_entries as _normalize_scope_entries,
    validate_scope_token as validate_scope_token,
)


def normalize_scope_entries(plugin_id: str, raw) -> list[dict]:
    """Normalize manifest scope entries to {token, description} dicts."""
    return [e.to_dict() for e in _normalize_scope_entries(plugin_id, raw)]


def set_plugin_scopes(plugin_id: str, raw) -> None:
    """Normalize, validate, and store scope entries for a plugin (replaces)."""
    _get_registry().register_plugin_permissions(plugin_id, raw, replace=True)


def get_plugin_scopes(plugin_id: str) -> list[dict]:
    """Return stored scope entries for plugin ([] if none)."""
    return [e.to_dict() for e in _get_registry().get_plugin_permissions(plugin_id)]


def get_all_scopes() -> list[dict]:
    """Return all scope entries with a plugin_id key on each row."""
    return _get_registry().all_permissions()


def get_all_tokens() -> list[str]:
    """Return deduplicated scope tokens across all plugins."""
    return _get_registry().all_tokens()


def clear(name: Optional[str] = None) -> None:
    """Clear a specific plugin's scopes or all if name is None."""
    _get_registry().unregister_plugin_permissions(name)


def mark_plugin_scopes_synthetic(plugin_id: str) -> None:
    """Flag a plugin as running on the synthesized fallback scope floor."""
    _get_registry().mark_synthetic(plugin_id)


def synthetic_scope_plugin_ids() -> list[str]:
    """Plugin ids currently running on the synthesized fallback floor."""
    return _get_registry().synthetic_plugin_ids()
