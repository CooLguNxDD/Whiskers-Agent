"""Request-scoped principal contextvar for in-process tool calls.

Playground ``/tools/invoke`` (and tests) call ``mcp.call_tool`` without an
HTTP access token. Middleware reads this contextvar when the token is absent.
"""

from __future__ import annotations

import contextvars
from typing import Sequence

from core.scope_management.principal import PrincipalKind, ScopeGrant

_request_principal: contextvars.ContextVar[ScopeGrant | None] = contextvars.ContextVar(
    "whiskers_request_principal", default=None
)


def set_request_principal(
    kind: PrincipalKind,
    role: str | None,
    scopes: Sequence[str] | None,
) -> contextvars.Token:
    """Set the current request principal; returns a token for reset()."""
    grant = ScopeGrant(
        scopes=list(scopes) if scopes is not None else None,
        role=role,
        kind=kind,
    )
    return _request_principal.set(grant)


def get_request_principal() -> ScopeGrant | None:
    """Return the current request principal, or None if unset."""
    return _request_principal.get()


def reset_request_principal(token: contextvars.Token) -> None:
    """Reset the principal contextvar to the value before set_request_principal."""
    _request_principal.reset(token)
