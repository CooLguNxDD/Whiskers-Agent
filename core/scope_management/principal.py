"""Principal kinds and access decision types for the scope subsystem."""

from dataclasses import dataclass
from enum import Enum


class PrincipalKind(str, Enum):
    """Who is calling — maps to the five access paths."""

    LOCAL_CLI = "local_cli"
    SESSION_USER = "session_user"
    API_KEY = "api_key"
    OAUTH_CLIENT = "oauth_client"
    ANONYMOUS = "anonymous"


@dataclass(frozen=True)
class ScopeGrant:
    """Resolved scopes for a principal at evaluation time.

    scopes=None means unrestricted (LOCAL_CLI / explicit CLI only after fail-closed).
    """

    scopes: list[str] | None = None
    role: str | None = None
    kind: PrincipalKind = PrincipalKind.API_KEY


@dataclass(frozen=True)
class AccessDecision:
    """Result of evaluating a ScopeGrant against a required token set."""

    allowed: bool
    reason: str
    required: frozenset[str]
