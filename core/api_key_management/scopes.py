"""core.api_key_management.scopes — backward-compat re-export shim.

Canonical implementation lives in ``core.scope_management``. All public names
are re-exported here so existing imports keep working.
"""

from core.scope_management import (  # noqa: F401
    caller_has_scope,
    coerce_force_execute,
    default_force_execute_authenticated,
    get_valid_scopes,
    is_allowed,
    playground_mcp_scopes,
    required_scopes_for_route,
    resolve_api_key_scopes,
    resolve_force_execute,
    role_defaults_force_execute,
    role_has_admin_bypass,
    scope_enforcement_enabled,
    scopes_for_role,
    Scope,
)

# Legacy private flag used by some tests to reset log-once state.
# policy module owns the real flag; keep a module-level alias for monkeypatch.
_scope_off_logged = False

__all__ = [
    "caller_has_scope",
    "coerce_force_execute",
    "default_force_execute_authenticated",
    "get_valid_scopes",
    "is_allowed",
    "playground_mcp_scopes",
    "required_scopes_for_route",
    "resolve_api_key_scopes",
    "resolve_force_execute",
    "role_defaults_force_execute",
    "role_has_admin_bypass",
    "scope_enforcement_enabled",
    "scopes_for_role",
    "Scope",
]
