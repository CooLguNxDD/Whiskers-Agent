"""Compat shim — API key store logic now lives in core.api_key_management.store.

Re-exports preserve `from db_layer.api_key_store import X` for existing
importers (oauth_service.py's local imports, and existing tests) without
duplicating logic.
"""

from core.api_key_management.store import (
    create_api_key,
    lookup_active_by_token,
    list_api_keys,
    revoke_api_key,
    rotate_api_key,
    delete_api_key,
)

__all__ = [
    "create_api_key",
    "lookup_active_by_token",
    "list_api_keys",
    "revoke_api_key",
    "rotate_api_key",
    "delete_api_key",
]
