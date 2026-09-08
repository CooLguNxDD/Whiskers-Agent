"""ScopeManager — ordered rule chain + principal resolvers (register pattern)."""

from __future__ import annotations

import logging
import threading
from typing import Any

from core.scope_management.principal import AccessDecision, ScopeGrant
from core.scope_management.registration import get_permission_registry
from core.scope_management.request import AccessRequest
from core.scope_management.rules import RuleSpec, default_rules
from core.scope_management.vocabulary import required_scopes_for_route

logger = logging.getLogger("whiskers")

_manager: ScopeManager | None = None
_manager_lock = threading.Lock()


class ScopeManager:
    """Owns ordered enforcement rules and principal resolvers."""

    def __init__(self) -> None:
        """Initialize empty rule chain."""
        self._rules: list[RuleSpec] = []
        self._lock = threading.Lock()

    def register_rule(self, rule: RuleSpec) -> None:
        """Append a rule to the ordered chain (isinstance-guarded)."""
        if not isinstance(rule, RuleSpec):
            raise TypeError("Only RuleSpec instances can be registered.")
        with self._lock:
            self._rules.append(rule)

    def evaluate(
        self,
        grant: ScopeGrant,
        *,
        plugin_id: str = "",
        tags: Any = None,
        tool_name: str = "",
        path: str = "",
        required: set[str] | frozenset[str] | None = None,
        request: AccessRequest | None = None,
    ) -> AccessDecision:
        """Run the rule chain; first decisive rule wins. Default deny.

        ``request`` may be supplied directly (new call sites); otherwise one
        is built from the loose kwargs so no existing caller must change.
        """
        from core.scope_management.policy import apply_mode

        if request is None:
            request = AccessRequest(
                plugin_id=plugin_id,
                tags=tuple(tags or ()),
                tool_name=tool_name,
                path=path,
            )

        if required is None:
            req = required_scopes_for_route(request.plugin_id, request.tags)
        else:
            req = set(required)
        required_fs = frozenset(req)

        with self._lock:
            rules = list(self._rules)

        for rule in rules:
            decision = rule.evaluate(
                grant, required_fs, plugin_id=request.plugin_id, path=request.path,
                request=request,
            )
            if decision is not None:
                return apply_mode(decision)

        return apply_mode(
            AccessDecision(False, "deny_default", required_fs)
        )

    @property
    def permissions(self):
        """The process-level PermissionRegistry backing dynamic vocab."""
        return get_permission_registry()

    def register_plugin_permissions(self, plugin_id: str, entries, *, replace: bool = True):
        """Front door: register a plugin/proxy's requested scope tokens."""
        return get_permission_registry().register_plugin_permissions(
            plugin_id, entries, replace=replace
        )

    def unregister_plugin_permissions(self, plugin_id: str | None = None) -> None:
        """Front door: clear a plugin/proxy's (or all) registered scope tokens."""
        get_permission_registry().unregister_plugin_permissions(plugin_id)


def _build_default_manager() -> ScopeManager:
    """Construct a ScopeManager with built-in rules registered."""
    mgr = ScopeManager()
    for rule in default_rules():
        mgr.register_rule(rule)
    return mgr


def get_scope_manager() -> ScopeManager:
    """Lazy singleton ScopeManager (registers default rules on first build)."""
    global _manager
    if _manager is not None:
        return _manager
    with _manager_lock:
        if _manager is None:
            _manager = _build_default_manager()
        return _manager


def _set_scope_manager(mgr: ScopeManager | None) -> None:
    """Test hook: replace or clear the singleton."""
    global _manager
    with _manager_lock:
        _manager = mgr
