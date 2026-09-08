"""Requirement providers — pluggable ``AccessRequest -> {required tokens}``.

Same register-pattern as ``ScopeManager.register_rule``: providers are tried
in order, contributions are unioned (OR semantics — holding any one token
from any one provider satisfies the request), matching the legacy
``required_scopes_for_route`` behaviour exactly.

``vocabulary.required_scopes_for_route(plugin_id, tags)`` stays a thin
adapter over ``default_resolver()`` so existing call sites
(``core/route_registry/execute.py``, ``operation_catalog.py``,
``core_graph/agent_loop/runner.py``, ``permission_gate.py``) do not churn.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Protocol

from core.scope_management.request import AccessRequest


class RequirementProvider(Protocol):
    """A named contributor of required-scope tokens for an AccessRequest."""

    name: str

    def provide(self, request: AccessRequest) -> frozenset[str]:
        """Resolve required scope tokens for the given request."""
        ...


@dataclass
class _ProviderSpec:
    """Named wrapper around a plain provider function, matching ``RuleSpec``'s shape."""

    name: str
    fn: Callable[[AccessRequest], frozenset[str]]

    def provide(self, request: AccessRequest) -> frozenset[str]:
        """Execute the provider's resolver function for the given request."""
        return self.fn(request)


def _plugin_provider(request: AccessRequest) -> frozenset[str]:
    """Today's formula: {plugin:<id>} ∪ {group:<id>:<tag> for tag in tags}.

    Plus the finer-grained ``plugin:<id>:<access>`` token (level-2 read/write
    split) so a plugin that has registered access-tagged permissions can be
    required at that grain without breaking callers still holding the coarse
    ``plugin:<id>`` token (grammar's ``expand_implied`` makes the coarse token
    a superset of the access-tagged ones).
    """
    if not request.plugin_id:
        return frozenset()
    out = {f"plugin:{request.plugin_id}"}
    if request.access is not None:
        out.add(f"plugin:{request.plugin_id}:{request.access.value}")
    for tag in request.tags or ():
        out.add(f"group:{request.plugin_id}:{tag}")
    return frozenset(out)


def _operation_provider(request: AccessRequest) -> frozenset[str]:
    """``op:<plugin_id>:<operation_id>`` fine-grained per-operation token."""
    if not request.plugin_id or not request.operation_id:
        return frozenset()
    return frozenset({f"op:{request.plugin_id}:{request.operation_id}"})


def _core_domain_provider(request: AccessRequest) -> frozenset[str]:
    """``core:<domain>:<access>`` — level-1 core-platform requirement."""
    if not request.core_domain:
        return frozenset()
    access = request.access.value if request.access is not None else "read"
    return frozenset({f"core:{request.core_domain}:{access}"})


def default_providers() -> list[_ProviderSpec]:
    """Ordered built-in providers."""
    return [
        _ProviderSpec("core_domain_provider", _core_domain_provider),
        _ProviderSpec("plugin_provider", _plugin_provider),
        _ProviderSpec("operation_provider", _operation_provider),
    ]


class RequirementResolver:
    """Ordered, registerable set of RequirementProviders; contributions OR."""

    def __init__(self) -> None:
        """Initialize with the built-in providers registered."""
        self._lock = threading.Lock()
        self._providers: list[_ProviderSpec] = list(default_providers())

    def register_provider(self, name: str, fn: Callable[[AccessRequest], frozenset[str]]) -> None:
        """Append a provider to the ordered chain."""
        if not callable(fn):
            raise TypeError("provider fn must be callable")
        with self._lock:
            self._providers.append(_ProviderSpec(name, fn))

    def resolve(self, request: AccessRequest) -> frozenset[str]:
        """Union every provider's contribution for this request."""
        with self._lock:
            providers = list(self._providers)
        out: set[str] = set()
        for provider in providers:
            out |= provider.provide(request)
        return frozenset(out)


_resolver: RequirementResolver | None = None
_resolver_lock = threading.Lock()


def get_requirement_resolver() -> RequirementResolver:
    """Lazy singleton RequirementResolver."""
    global _resolver
    if _resolver is not None:
        return _resolver
    with _resolver_lock:
        if _resolver is None:
            _resolver = RequirementResolver()
        return _resolver


def _set_requirement_resolver(resolver: RequirementResolver | None) -> None:
    """Test hook: replace or clear the singleton."""
    global _resolver
    with _resolver_lock:
        _resolver = resolver


def resolve_requirements(request: AccessRequest) -> frozenset[str]:
    """Module-level convenience: resolve via the singleton resolver."""
    return get_requirement_resolver().resolve(request)
