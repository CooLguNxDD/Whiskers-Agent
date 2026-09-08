"""Scope sentinel tokens and storage normalization."""

from enum import Enum


class Scope(str, Enum):
    """Unified enum for core scopes in the system."""

    ALL = "all"
    WILDCARD = "*"
    ADMIN = "admin"


SCOPE_ALL = Scope.ALL
SCOPE_WILDCARD = Scope.WILDCARD
SCOPE_ADMIN = Scope.ADMIN


def normalize_scopes_for_storage(scopes: list[str] | None) -> list[str]:
    """Normalize scopes before DB write.

    None (UI "all selected") and any list containing all/* collapse to ["all"].
    Empty list stays empty (deny-all). Other lists pass through verbatim.
    Idempotent: ["all"] → ["all"].
    """
    if scopes is None:
        return [SCOPE_ALL]
    if SCOPE_ALL in scopes or SCOPE_WILDCARD in scopes:
        return [SCOPE_ALL]
    return list(scopes)
