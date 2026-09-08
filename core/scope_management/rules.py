"""Built-in scope evaluation rules (ordered chain, first decisive wins)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Protocol

from core.scope_management.grammar import (
    ScopeKind,
    expand_implied,
    grant_covers_core,
    parse_scope,
    plugin_grant_covers,
)
from core.scope_management.principal import AccessDecision, PrincipalKind, ScopeGrant
from core.scope_management.request import AccessRequest
from core.scope_management.sentinels import SCOPE_ADMIN, SCOPE_ALL, SCOPE_WILDCARD

logger = logging.getLogger("whiskers")


REASON_DENY_ANONYMOUS = "deny_anonymous"


class Rule(Protocol):
    """Rule protocol: evaluate returns AccessDecision or None (not decisive)."""

    name: str

    def evaluate(
        self,
        grant: ScopeGrant,
        required: frozenset[str],
        *,
        plugin_id: str = "",
        path: str = "",
        request: AccessRequest | None = None,
    ) -> AccessDecision | None: ...


@dataclass
class RuleSpec:
    """Named rule wrapper used by ScopeManager.register_rule."""

    name: str
    evaluate_fn: Callable[..., AccessDecision | None]

    def evaluate(
        self,
        grant: ScopeGrant,
        required: frozenset[str],
        *,
        plugin_id: str = "",
        path: str = "",
        request: AccessRequest | None = None,
    ) -> AccessDecision | None:
        """Evaluate the rule against the given grant and required scopes."""
        return self.evaluate_fn(
            grant, required, plugin_id=plugin_id, path=path, request=request
        )


def _enforcement_off(
    grant: ScopeGrant,
    required: frozenset[str],
    *,
    plugin_id: str = "",
    path: str = "",
    request: AccessRequest | None = None,
) -> AccessDecision | None:
    from core.scope_management.policy import scope_enforcement_enabled

    if not scope_enforcement_enabled():
        return AccessDecision(True, "bypass_enforcement_off", required)
    return None


def _unrestricted_none(
    grant: ScopeGrant,
    required: frozenset[str],
    *,
    plugin_id: str = "",
    path: str = "",
    request: AccessRequest | None = None,
) -> AccessDecision | None:
    """None scopes → allow only for LOCAL_CLI / local stdio (fail-closed)."""
    if grant.scopes is not None:
        return None

    if grant.kind == PrincipalKind.LOCAL_CLI:
        return AccessDecision(True, "bypass_unrestricted", required)
    try:
        from core.context.transport import is_local_stdio

        if is_local_stdio():
            return AccessDecision(True, "bypass_unrestricted", required)
    except Exception:
        # Transport probe failed — fall through to deny-by-default for None scopes.
        logger.debug("scope_rules: is_local_stdio probe failed", exc_info=True)
    # Fall through — anonymous/network None is denied by later default
    return None


def _admin_bypass(
    grant: ScopeGrant,
    required: frozenset[str],
    *,
    plugin_id: str = "",
    path: str = "",
    request: AccessRequest | None = None,
) -> AccessDecision | None:
    if grant.scopes is not None and SCOPE_ADMIN in grant.scopes:
        return AccessDecision(True, "bypass_admin", required)
    return None


def _all_wildcard(
    grant: ScopeGrant,
    required: frozenset[str],
    *,
    plugin_id: str = "",
    path: str = "",
    request: AccessRequest | None = None,
) -> AccessDecision | None:
    """Bug A fix: 'all' or '*' in scopes → full bypass."""
    if grant.scopes is None:
        return None
    if SCOPE_ALL in grant.scopes or SCOPE_WILDCARD in grant.scopes:
        return AccessDecision(True, "bypass_all", required)
    return None


def _plugin_gate_ceiling(
    grant: ScopeGrant,
    required: frozenset[str],
    *,
    plugin_id: str = "",
    path: str = "",
    request: AccessRequest | None = None,
) -> AccessDecision | None:
    """Level-3 ceiling: constrains what a gated plugin's own request may reach.

    Runs after ``admin_bypass``/``all_wildcard`` (positions 3-4) so admin and
    ``all``/``*`` grants already returned before reaching here — the gate
    constrains scoped principals only, never admin. A plugin with no
    registered gate (the common case pre-Stage-6 backfill) is unconstrained:
    absence of a gate is "no ceiling", not "deny everything".

    Only denies the specific facet the request actually names (operation_id
    / core_domain / http_path) — a request that names none of those passes
    through to the normal intersection rule untouched.
    """
    if request is None or not request.plugin_id:
        return None

    from core.scope_management.gates import get_plugin_gate_registry

    gate = get_plugin_gate_registry().get_gate(request.plugin_id)
    if gate is None:
        return None

    if request.operation_id and not gate.permits_operation(request.operation_id):
        return AccessDecision(False, "deny_gate_operation", required)

    if request.core_domain:
        access = request.access.value if request.access is not None else "read"
        token = f"core:{request.core_domain}:{access}"
        if not gate.permits_core(token):
            return AccessDecision(False, "deny_gate_core", required)

    if request.http_path and not gate.permits_endpoint(request.http_path):
        return AccessDecision(False, "deny_gate_endpoint", required)

    return None


def _empty_required(
    grant: ScopeGrant,
    required: frozenset[str],
    *,
    plugin_id: str = "",
    path: str = "",
    request: AccessRequest | None = None,
) -> AccessDecision | None:
    """Empty required set: unrestricted allow; authenticated → deny (C09)."""
    if required:
        return None

    # Unrestricted (None scopes) already handled; if we get here with None,
    # LOCAL_CLI path was not taken — still allow empty required only for CLI.
    if grant.scopes is None:
        return AccessDecision(True, "allow_empty_required", required)

    # Authenticated (including empty list) + unresolved plugin → deny
    return AccessDecision(False, "deny_no_plugin_scope", required)


def _tag_intersection(
    grant: ScopeGrant,
    required: frozenset[str],
    *,
    plugin_id: str = "",
    path: str = "",
    request: AccessRequest | None = None,
) -> AccessDecision | None:
    """Literal intersection, widened by grammar-aware coverage.

    Two additive layers on top of a plain set intersection (never narrower —
    only ever adds more ways to satisfy ``required``):

    - **closure**: a held token's ``expand_implied`` set (``core:<d>:write``
      covers ``core:<d>:read``; bare ``plugin:<id>`` covers its own write/
      read/group/op tokens) is intersected against ``required`` too.
    - **hierarchy**: a held ``core:<domain>:<access>`` token covers a
      required token on a *narrower* sub-domain (``core:whiskers:write``
      covers ``core:whiskers.proxy:read``) via ``grant_covers_core``; a held
      plugin/group/op token covers a required group/op token on the same
      plugin via ``plugin_grant_covers``. Neither is expressible as a flat
      set membership check, so they run as an explicit second pass only
      when the literal+closure pass found nothing.
    """
    if grant.scopes is None:
        return None

    from core.scope_management.legacy_map import expand_legacy_alias_for_read

    held = set(grant.scopes)
    for tok in list(held):
        held |= expand_legacy_alias_for_read(tok)

    expanded_held: set[str] = set()
    for tok in held:
        expanded_held |= expand_implied(tok)

    if required & expanded_held:
        return AccessDecision(True, "intersection", required)

    for req_tok in required:
        parsed_req = parse_scope(req_tok)
        if parsed_req.kind == ScopeKind.CORE:
            if any(
                grant_covers_core(held_tok, parsed_req.id_or_domain, parsed_req.access)
                for held_tok in held
            ):
                return AccessDecision(True, "intersection", required)
        elif parsed_req.kind in (ScopeKind.GROUP, ScopeKind.OP):
            group_tag = parsed_req.tag_or_op if parsed_req.kind == ScopeKind.GROUP else ""
            op_id = parsed_req.tag_or_op if parsed_req.kind == ScopeKind.OP else ""
            if any(
                plugin_grant_covers(held_tok, parsed_req.id_or_domain, group_tag=group_tag, op_id=op_id)
                for held_tok in held
            ):
                return AccessDecision(True, "intersection", required)

    return AccessDecision(False, "deny_intersection", required)


def _deny_anonymous(
    grant: ScopeGrant,
    required: frozenset[str],
    *,
    plugin_id: str = "",
    path: str = "",
    request: AccessRequest | None = None,
) -> AccessDecision | None:
    """Explicit deny for ANONYMOUS principals (C07)."""
    if grant.kind == PrincipalKind.ANONYMOUS:
        return AccessDecision(False, REASON_DENY_ANONYMOUS, required)
    return None


def default_rules() -> list[RuleSpec]:
    """Ordered built-in rules: off → unrestricted → admin → all → gate → anon → empty → intersect.

    ``plugin_gate_ceiling`` runs after admin/all bypass (so admin and
    ``all``/``*`` grants are unaffected by any plugin's ceiling) and before
    ``deny_anonymous``/``empty_required``/``tag_intersection`` (so a gate
    denial is decisive before the normal caller-scope checks run).

    ``deny_anonymous`` runs before empty_required/tag_intersection so ANONYMOUS
    principals get ``REASON_DENY_ANONYMOUS`` (middleware → "authentication required")
    instead of a generic intersection deny.
    """
    return [
        RuleSpec("enforcement_off", _enforcement_off),
        RuleSpec("unrestricted_none", _unrestricted_none),
        RuleSpec("admin_bypass", _admin_bypass),
        RuleSpec("all_wildcard", _all_wildcard),
        RuleSpec("plugin_gate_ceiling", _plugin_gate_ceiling),
        RuleSpec(REASON_DENY_ANONYMOUS, _deny_anonymous),
        RuleSpec("empty_required", _empty_required),
        RuleSpec("tag_intersection", _tag_intersection),
    ]
