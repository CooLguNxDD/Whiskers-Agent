"""Role → scopes helpers (moved from api_key_management.scopes)."""

from __future__ import annotations

import logging

from core.scope_management.vocabulary import get_valid_scopes
from core.scope_management.sentinels import Scope

_logger = logging.getLogger("whiskers")


def resolve_api_key_scopes(row: dict) -> list[str]:
    """Resolve AccessToken scopes for a looked-up API key row.

    NULL/missing ``scopes`` => deny-all by default; full access only under the
    ``legacy_full`` security policy (logged). Empty list => deny-all.
    Non-empty list => exactly those tokens (including ``["all"]``).
    """
    import utils.server_config

    scopes = row.get("scopes")
    if scopes is None:
        if utils.server_config.API_KEY_NULL_SCOPES_POLICY == "legacy_full":
            _logger.warning(
                "API key has NULL/missing scopes, falling back to legacy full access scopes."
            )
            return get_valid_scopes()
        return []
    return list(scopes)


def scopes_for_role(role: str) -> list[str]:
    """Resolve config-driven scopes for a user role against the vocabulary.

    Seeds from the role's declared explicit tokens (widened with expand_implied),
    then includes plugin tokens if ``include_plugin_tokens`` is configured
    (``True`` -> all plugin tokens; ``"read"`` -> only read-accessible tokens).
    """
    import utils.server_config
    from core.scope_management.grammar import expand_implied, parse_scope, ScopeKind

    roles_config = utils.server_config.ROLES_CONFIG
    if role not in roles_config:
        _logger.warning("Unknown role %r requested, returning empty scopes list.", role)
        return []

    role_cfg = roles_config[role]
    valid_scopes = get_valid_scopes()
    scopes_pool: set[str] = set()

    role_scopes = role_cfg.get("scopes", [])
    if role_scopes == "*":
        return list(valid_scopes)
    elif isinstance(role_scopes, list):
        for s in role_scopes:
            scopes_pool.add(s)
            scopes_pool.update(expand_implied(s))

    include_plugin = role_cfg.get("include_plugin_tokens")
    # Also support legacy include_data_plugins / include_groups_matching if an un-migrated role dict is passed
    if include_plugin is True or role_cfg.get("include_data_plugins"):
        for s in valid_scopes:
            parsed = parse_scope(s)
            if parsed.kind in (ScopeKind.PLUGIN, ScopeKind.GROUP, ScopeKind.OP):
                scopes_pool.add(s)
                scopes_pool.update(expand_implied(s))
    elif include_plugin == "read":
        for s in valid_scopes:
            parsed = parse_scope(s)
            if parsed.kind in (ScopeKind.PLUGIN, ScopeKind.GROUP, ScopeKind.OP):
                if parsed.access == "read" or "read" in parsed.tag_or_op.lower() or parsed.raw.endswith(":read"):
                    scopes_pool.add(s)
                    scopes_pool.update(expand_implied(s))
    elif role_cfg.get("include_groups_matching"):
        matching = role_cfg.get("include_groups_matching", [])
        for s in valid_scopes:
            parsed = parse_scope(s)
            if parsed.kind == ScopeKind.GROUP:
                if any(sub in parsed.tag_or_op for sub in matching):
                    scopes_pool.add(s)
                    scopes_pool.update(expand_implied(s))

    exclude_prefixes = role_cfg.get("exclude_prefixes", [])
    if exclude_prefixes:
        final_scopes = [
            s for s in scopes_pool
            if not any(s.startswith(p) or s == p for p in exclude_prefixes)
        ]
        return sorted(final_scopes)

    return sorted(scopes_pool)


def role_has_admin_bypass(role: str | None) -> bool:
    """True when the role is configured to receive the ``admin`` master scope."""
    if not role:
        return False
    import utils.server_config

    return role in utils.server_config.ADMIN_BYPASS_ROLES


def playground_mcp_scopes(role: str | None) -> list[str]:
    """Resolve scopes for a playground MCP Bearer minted from a session cookie.

    Unresolved/empty role returns [] (deny-all floor for minting). Callers that
    want vocabulary floor should pass an explicit role or call get_valid_scopes().
    """
    if role_has_admin_bypass(role):
        return sorted(set(get_valid_scopes()) | {Scope.ADMIN})
    if role:
        return scopes_for_role(role)
    return []


def coerce_force_execute(value) -> bool:
    """Normalize wire/client force_execute values to a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def default_force_execute_authenticated(authenticated: bool) -> bool:
    """Authenticated principals default force_execute=True; scopes authorize tools."""
    return bool(authenticated)


def role_defaults_force_execute(role: str | None) -> bool:
    """Default force_execute from a role claim (any non-empty role → True).

    Role list ``FORCE_EXECUTE_ROLES`` is no longer the gate. Authorization is
    enforced by ScopeManager / the permission registry on each graph step.
    Unauthenticated / missing role → False.
    """
    return bool(role)


def resolve_force_execute(
    body: dict,
    role: str | None = None,
    *,
    authenticated: bool | None = None,
) -> bool:
    """Resolve force_execute from body (explicit wins) or auth default.

    When ``force_execute`` is omitted:
    - if ``authenticated`` is provided, use that (True → force on)
    - else fall back to ``role_defaults_force_execute(role)`` (any role → True)
    """
    if "force_execute" in body:
        return coerce_force_execute(body["force_execute"])
    if authenticated is not None:
        return default_force_execute_authenticated(authenticated)
    return role_defaults_force_execute(role)


def caller_has_scope(caller_scopes, token: str) -> bool:
    """True when scope enforcement is off or token (or its legacy/grammar
    alias — see ``legacy_map.expand_legacy_alias_for_read``) is present in
    caller_scopes.

    The alias widening matters post-``core_047_scope_cutover``: a caller
    holding the migrated ``core:terminal:write`` must still satisfy a call
    site still checking the legacy ``terminal:use`` string (the terminal
    relay's raw checks are deliberately not migrated off this helper's
    literal-token callers this branch).
    """
    from core.scope_management.legacy_map import expand_legacy_alias_for_read
    from core.scope_management.policy import scope_enforcement_enabled

    if not scope_enforcement_enabled():
        return True
    if caller_scopes is None:
        return True
    return bool(expand_legacy_alias_for_read(token) & set(caller_scopes))
