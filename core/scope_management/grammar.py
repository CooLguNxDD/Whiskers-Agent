"""Scope token grammar — parser + implied-scope closure.

One grammar spans all three permission levels::

    scope     := sentinel | core | plugin | group | op
    sentinel  := "all" | "*" | "admin"
    core      := "core:" <domain> ":" <access>          # level 1
    plugin    := "plugin:" <id> [":" <access>]           # level 2
    group     := "group:" <id> ":" <tag>                 # level 2 (unchanged)
    op        := "op:" <id> ":" <operation_id>            # level 2
    domain    := <seg> ["." <seg>]                        # e.g. "whiskers.proxy"
    access    := "read" | "write"

``expand_implied(token)`` computes the hierarchy a *grant* carries so callers
stop hand-rolling prefix/substring checks (``include_data_plugins`` /
``exclude_prefixes`` / ``include_groups_matching``):

- ``core:<domain>:write`` implies ``core:<domain>:read``
- ``core:<domain>:*`` implies ``core:<domain>.<sub>:*`` for any sub-domain
- ``plugin:<id>`` implies ``plugin:<id>:write`` implies ``plugin:<id>:read``
  implies every ``group:<id>:*`` and ``op:<id>:*`` (closure only stops there —
  it never invents concrete group/op tags out of thin air; those come from
  the registry)

Closure is applied to the **grant** only, never to a required set — required
sets stay small and OR semantics (any one held token satisfies) survive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_SENTINELS = frozenset({"all", "*", "admin"})

_SEG = r"[a-zA-Z0-9_-]+"
_DOMAIN_RE = re.compile(rf"^{_SEG}(?:\.{_SEG})*$")
_ID_RE = re.compile(rf"^{_SEG}$")
_TAG_RE = re.compile(r"^\S+$")
_OPERATION_ID_RE = re.compile(r"^\S+$")

_CORE_RE = re.compile(rf"^core:({_SEG}(?:\.{_SEG})*):(read|write)$")
_PLUGIN_RE = re.compile(rf"^plugin:({_SEG})(?::(read|write))?$")
_GROUP_RE = re.compile(rf"^group:({_SEG}):(\S+)$")
_OP_RE = re.compile(rf"^op:({_SEG}):(\S+)$")


class ScopeKind(str, Enum):
    """Which grammar production a parsed token matched."""

    SENTINEL = "sentinel"
    CORE = "core"
    PLUGIN = "plugin"
    GROUP = "group"
    OP = "op"
    INVALID = "invalid"


@dataclass(frozen=True)
class ParsedScope:
    """A parsed scope token, decomposed into its grammar parts."""

    raw: str
    kind: ScopeKind
    id_or_domain: str = ""       # domain (core) | plugin id (plugin/group/op)
    access: str | None = None    # "read" | "write" (core, optional plugin)
    tag_or_op: str = ""          # group tag | op operation_id


def parse_scope(token: str) -> ParsedScope:
    """Parse a single scope token into its grammar production.

    Never raises — an unrecognized shape parses to ``ScopeKind.INVALID`` so
    callers can decide fail-open (ignore) vs fail-closed (reject) themselves.
    """
    if not token:
        return ParsedScope(raw=token, kind=ScopeKind.INVALID)

    if token in _SENTINELS:
        return ParsedScope(raw=token, kind=ScopeKind.SENTINEL)

    m = _CORE_RE.match(token)
    if m:
        return ParsedScope(
            raw=token, kind=ScopeKind.CORE, id_or_domain=m.group(1), access=m.group(2)
        )

    m = _PLUGIN_RE.match(token)
    if m:
        return ParsedScope(
            raw=token, kind=ScopeKind.PLUGIN, id_or_domain=m.group(1), access=m.group(2)
        )

    m = _GROUP_RE.match(token)
    if m:
        return ParsedScope(
            raw=token, kind=ScopeKind.GROUP, id_or_domain=m.group(1), tag_or_op=m.group(2)
        )

    m = _OP_RE.match(token)
    if m:
        return ParsedScope(
            raw=token, kind=ScopeKind.OP, id_or_domain=m.group(1), tag_or_op=m.group(2)
        )

    return ParsedScope(raw=token, kind=ScopeKind.INVALID)


def is_valid_scope_token(token: str) -> bool:
    """True when token parses to a recognized grammar production."""
    return parse_scope(token).kind != ScopeKind.INVALID


def _domain_ancestors(domain: str) -> list[str]:
    """``"whiskers.proxy"`` -> ``["whiskers.proxy", "whiskers"]`` (self first)."""
    parts = domain.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))][::-1]


def expand_implied(token: str) -> frozenset[str]:
    """Return the closure of tokens a grant holding ``token`` also satisfies.

    Always includes ``token`` itself. Sentinels and unparseable tokens expand
    to just themselves — sentinel bypass logic lives in the rule chain, not
    here.
    """
    parsed = parse_scope(token)
    out: set[str] = {token}

    if parsed.kind == ScopeKind.CORE:
        # core:<domain>:write -> core:<domain>:read (same domain only; cross-domain
        # whiskers.* -> whiskers.<sub>.* implication is a REQUIRED-set concern, handled
        # by the caller matching a broader held token against a narrower ancestor
        # domain via required-set construction, not by expanding the grant here).
        if parsed.access == "write":
            out.add(f"core:{parsed.id_or_domain}:read")

    elif parsed.kind == ScopeKind.PLUGIN:
        pid = parsed.id_or_domain
        if parsed.access is None:
            # Bare plugin:<id> is the widest plugin-level grant.
            out.add(f"plugin:{pid}:write")
            out.add(f"plugin:{pid}:read")
        elif parsed.access == "write":
            out.add(f"plugin:{pid}:read")

    return frozenset(out)


def core_domain_covers(held_domain: str, required_domain: str) -> bool:
    """True when a grant on ``held_domain`` covers ``required_domain``.

    ``held_domain`` covers itself and any of its sub-domains: holding
    ``"whiskers"`` covers ``"whiskers.proxy"``; holding ``"whiskers.proxy"`` does
    NOT cover ``"whiskers"`` or its sibling ``"whiskers.console"``.
    """
    if held_domain == required_domain:
        return True
    return required_domain.startswith(f"{held_domain}.")


def grant_covers_core(held_token: str, required_domain: str, required_access: str) -> bool:
    """True when holding ``held_token`` (a ``core:...`` scope) satisfies a
    required ``core:<required_domain>:<required_access>``.

    Combines domain-hierarchy coverage with read/write closure: holding
    ``core:whiskers:write`` covers ``core:whiskers.proxy:read``.
    """
    parsed = parse_scope(held_token)
    if parsed.kind != ScopeKind.CORE:
        return False
    if not core_domain_covers(parsed.id_or_domain, required_domain):
        return False
    if required_access == "read":
        return True  # write or read held -> read satisfied
    return parsed.access == "write"


def plugin_grant_covers(held_token: str, plugin_id: str, *, group_tag: str = "", op_id: str = "") -> bool:
    """True when holding ``held_token`` covers a plugin-scoped requirement.

    ``held_token`` may be ``plugin:<id>`` (widest), ``plugin:<id>:write``,
    ``plugin:<id>:read``, ``group:<id>:<tag>``, or ``op:<id>:<op_id>``.
    """
    parsed = parse_scope(held_token)
    if parsed.kind == ScopeKind.PLUGIN and parsed.id_or_domain == plugin_id:
        return True  # any plugin:<id>[:access] token authorizes group/op requirements
    if group_tag and parsed.kind == ScopeKind.GROUP:
        return parsed.id_or_domain == plugin_id and parsed.tag_or_op == group_tag
    if op_id and parsed.kind == ScopeKind.OP:
        return parsed.id_or_domain == plugin_id and parsed.tag_or_op == op_id
    return False
