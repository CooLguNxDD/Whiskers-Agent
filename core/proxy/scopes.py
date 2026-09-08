"""API-key scope registration helpers for mounted proxy servers.

Extracted from ``proxy_manager`` so mount lifecycle stays separate from
permission vocabulary bookkeeping.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("whiskers")

_PROXY_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_proxy_name(name: str) -> bool:
    """Return True when a proxy name is safe for scope tokens and plugin ids."""
    return bool(name and _PROXY_NAME_RE.match(name))


def register_proxy_scopes(name: str) -> None:
    """Register API-key scope tokens for a mounted proxy."""
    if not validate_proxy_name(name):
        logger.warning("ProxyManager: skipping scope registration for invalid name %r", name)
        return
    from core.scope_management.registration import register_plugin_permissions

    pid = f"proxy_{name}"
    register_plugin_permissions(
        pid,
        [
            {"token": f"plugin:{pid}", "description": f"All tools on proxy '{name}'"},
            {"token": f"group:{pid}:proxy", "description": f"Proxy route group for '{name}'"},
        ],
        replace=True,
    )


def clear_proxy_scopes(name: str) -> None:
    """Drop contributed scope tokens when a proxy is unmounted or deleted."""
    from core.scope_management.registration import unregister_plugin_permissions

    unregister_plugin_permissions(f"proxy_{name}")


# Back-compat private aliases used by proxy_manager.
_validate_proxy_name = validate_proxy_name
_register_proxy_scopes = register_proxy_scopes
_clear_proxy_scopes = clear_proxy_scopes
