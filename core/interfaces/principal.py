"""Resolved caller identity returned by ``IAuthService``."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    """Who the caller is, and what they're allowed to do.

    ``scopes`` is a plain ``frozenset`` — callers still route the actual
    allow/deny decision through ``core.scope_management.evaluate_access``;
    this dataclass only carries the resolved identity.
    """

    subject: str
    scopes: frozenset[str]
    # Session-user role claim (``ocat_role``) — feeds ScopeGrant.role for admin-bypass
    # evaluation. Always None for an API-key-derived principal.
    role: str | None = None
