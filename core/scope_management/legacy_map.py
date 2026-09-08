"""LEGACY_SCOPE_MAP — free-token to level-1 grammar token migration map.

Shared by two consumers:

- ``migrations/versions/core/core_047_scope_cutover.py`` rewrites every
  stored scope column through this map (downgrade inverts it).
- Runtime read-side normalization (Stage 4's terminal compatibility shim):
  the 11 raw ``terminal:use``/``terminal:host`` string-check sites are not
  migrated off ``caller_has_scope`` on this branch, so a legacy token must
  keep working as an alias of its new grammar token on the *read* side even
  after the DB migration has rewritten stored rows.

A token mapping to ``None`` is intentionally dropped (unmapped legacy noise,
e.g. a role-config token no manifest ever declared) — logged by the caller,
never silently kept.
"""

from __future__ import annotations

LEGACY_SCOPE_MAP: dict[str, str | None] = {
    "terminal:use": "core:terminal:write",
    "terminal:host": "core:terminal:read",
    "whiskers_agent": "core:graph:write",
    "whiskers": "core:graph:write",
    "opencat": "core:graph:write",
    "sandbox:exec": "core:terminal.sandbox:write",
}

# Tokens that pass through unchanged — sentinels and the already-grammar-
# shaped plugin/group namespace. Listed explicitly so the migration can
# assert every input token was considered, not silently skipped.
UNCHANGED_SCOPE_PREFIXES: tuple[str, ...] = ("plugin:", "group:", "op:", "core:")
UNCHANGED_SCOPE_TOKENS: frozenset[str] = frozenset({"admin", "all", "*"})


def is_unchanged_token(token: str) -> bool:
    """True when ``token`` needs no rewrite (sentinel or already-grammar-shaped)."""
    if token in UNCHANGED_SCOPE_TOKENS:
        return True
    return any(token.startswith(p) for p in UNCHANGED_SCOPE_PREFIXES)


def map_legacy_token(token: str) -> str | None:
    """Return the migrated token, the unchanged token, or None (unmapped/drop)."""
    if is_unchanged_token(token):
        return token
    return LEGACY_SCOPE_MAP.get(token)


def map_legacy_tokens(tokens: list[str]) -> list[str]:
    """Map a whole scope list, dropping unmapped tokens (logged by the caller)."""
    out: list[str] = []
    for t in tokens:
        mapped = map_legacy_token(t)
        if mapped is not None and mapped not in out:
            out.append(mapped)
    return out


def expand_legacy_alias_for_read(token: str) -> frozenset[str]:
    """Read-side compatibility: a legacy token also satisfies its new alias.

    Used by the terminal relay's still-raw ``caller_has_scope`` checks so a
    grant already migrated (or minted fresh) under the new grammar token
    still authorizes a check written against the legacy string, and vice
    versa — the migration is one-way on storage, but reads accept both
    shapes until the terminal relay's Stage 6 rewrite.
    """
    out = {token}
    mapped = LEGACY_SCOPE_MAP.get(token)
    if mapped:
        out.add(mapped)
    for legacy, new in LEGACY_SCOPE_MAP.items():
        if new == token:
            out.add(legacy)
    return frozenset(out)
