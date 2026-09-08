"""core.scope_management — unified scope-permission subsystem.

Single entrypoint for all five access paths (session, CLI, OAuth, API key,
anonymous). ScopeManager owns ordered rules + principal resolvers.
"""

from core.scope_management.principal import (
    AccessDecision,
    PrincipalKind,
    ScopeGrant,
)
from core.scope_management.sentinels import (
    SCOPE_ADMIN,
    SCOPE_ALL,
    SCOPE_WILDCARD,
    Scope,
    normalize_scopes_for_storage,
)
from core.scope_management.vocabulary import get_valid_scopes, required_scopes_for_route
from core.scope_management.manager import get_scope_manager, _set_scope_manager
from core.scope_management.policy import scope_enforcement_enabled
from core.scope_management.registration import (
    PermissionRegistry,
    ScopePermission,
    get_permission_registry,
    register_plugin_permissions,
    unregister_plugin_permissions,
)
from core.scope_management.roles import (
    caller_has_scope,
    coerce_force_execute,
    default_force_execute_authenticated,
    playground_mcp_scopes,
    resolve_api_key_scopes,
    resolve_force_execute,
    role_defaults_force_execute,
    role_has_admin_bypass,
    scopes_for_role,
)
from core.scope_management.context import (
    get_request_principal,
    reset_request_principal,
    set_request_principal,
)

from core.scope_management.rules import REASON_DENY_ANONYMOUS

__all__ = [
    "REASON_DENY_ANONYMOUS",
    "AccessDecision",
    "PrincipalKind",
    "ScopeGrant",
    "Scope",
    "SCOPE_ADMIN",
    "SCOPE_ALL",
    "SCOPE_WILDCARD",
    "normalize_scopes_for_storage",
    "get_valid_scopes",
    "required_scopes_for_route",
    "get_scope_manager",
    "_set_scope_manager",
    "evaluate_access",
    "is_allowed",
    "scope_enforcement_enabled",
    "caller_has_scope",
    "playground_mcp_scopes",
    "resolve_api_key_scopes",
    "coerce_force_execute",
    "default_force_execute_authenticated",
    "resolve_force_execute",
    "role_defaults_force_execute",
    "role_has_admin_bypass",
    "scopes_for_role",
    "get_request_principal",
    "reset_request_principal",
    "set_request_principal",
    "PermissionRegistry",
    "ScopePermission",
    "get_permission_registry",
    "register_plugin_permissions",
    "unregister_plugin_permissions",
]


def evaluate_access(
    grant: ScopeGrant,
    *,
    plugin_id: str = "",
    tags=None,
    tool_name: str = "",
    path: str = "",
    required: set[str] | frozenset[str] | None = None,
    request=None,
) -> AccessDecision:
    """Module-level convenience: evaluate grant via the singleton manager.

    ``request`` (an ``AccessRequest``) may be passed directly for callers
    that need the level-1/level-3 fields (``core_domain``, ``operation_id``,
    ``access``, ``http_path``) the loose kwargs don't cover; when given, it
    takes precedence over the loose kwargs exactly as ``ScopeManager.evaluate``
    already does.
    """
    return get_scope_manager().evaluate(
        grant,
        plugin_id=plugin_id,
        tags=tags,
        tool_name=tool_name,
        path=path,
        required=required,
        request=request,
    )


def is_allowed(caller_scopes, required: set[str]) -> bool:
    """Compat: same contract as legacy scopes.is_allowed.

    Builds an ad-hoc ScopeGrant and runs the rule chain with ``required``
    passed directly (no route lookup). ``caller_scopes is None`` is treated as
    unrestricted (LOCAL_CLI kind) to preserve the legacy skip-check contract.
    """
    grant = ScopeGrant(
        scopes=list(caller_scopes) if caller_scopes is not None else None,
        role=None,
        kind=PrincipalKind.LOCAL_CLI if caller_scopes is None else PrincipalKind.API_KEY,
    )
    decision = evaluate_access(grant, required=set(required) if required else set(), path="compat")
    return decision.allowed
