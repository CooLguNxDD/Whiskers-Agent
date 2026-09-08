"""PluginGateSpec — level-3 ceiling on what a plugin itself may reach.

Mirrors ``core_graph.model_roles.role_spec`` deliberately: a frozen
dataclass, a strict ``_*_KEYS`` allowlist, and a fail-closed parser that
never silently repairs a bad spec. A plugin declares its own gate in
``manifest.json``'s top-level ``"gate"`` key; an operator may replace it
(never merge — see ``gate_overlay.py``) via a DB override.

The gate constrains three axes independently (a request must clear all that
apply):

- ``core``: the set of ``core:<domain>:<access>`` tokens (or bare domains,
  defaulting to ``access``) the plugin's own operations may reach when they
  call back into core (e.g. ``run_graph``, config reads).
- ``operations``: an allow/deny list of the plugin's own ``operation_id``s
  reachable at all (independent of who is calling — this caps the plugin's
  surface, not the caller's scopes).
- ``endpoints``: glob-matched HTTP path prefixes the plugin's routes may
  expose.

Absence of a ``gate`` block means "no ceiling" (today's unconstrained
behaviour) — this is additive, not a default-deny change.
"""

from __future__ import annotations

import fnmatch
import threading
from dataclasses import dataclass, field
from typing import Any


class GateSpecError(ValueError):
    """Raised when a plugin gate spec dict fails validation. Never silently repaired."""


def _opt_str_tuple(d: dict, key: str, *, where: str) -> tuple[str, ...]:
    v = d.get(key, [])
    if v is None:
        return ()
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise GateSpecError(f"{where}: '{key}' must be a list of non-empty strings")
    return tuple(v)


_OPERATIONS_KEYS = {"allow", "deny"}


@dataclass(frozen=True)
class OperationGate:
    """Allow/deny list over the plugin's own ``operation_id``s.

    ``allow`` defaults to ``("*",)`` (everything the plugin declares);
    ``deny`` always wins over ``allow`` for an overlapping id/wildcard.
    """

    allow: tuple[str, ...] = ("*",)
    deny: tuple[str, ...] = ()

    def permits(self, operation_id: str) -> bool:
        """True when ``operation_id`` clears this allow/deny list."""
        if any(fnmatch.fnmatchcase(operation_id, pat) for pat in self.deny):
            return False
        return any(fnmatch.fnmatchcase(operation_id, pat) for pat in self.allow)


def _parse_operations(d: Any, *, where: str) -> OperationGate:
    if d is None:
        return OperationGate()
    if not isinstance(d, dict):
        raise GateSpecError(f"{where}.operations: must be an object")
    unknown = set(d.keys()) - _OPERATIONS_KEYS
    if unknown:
        raise GateSpecError(f"{where}.operations: unknown keys {sorted(unknown)}")
    allow = _opt_str_tuple(d, "allow", where=f"{where}.operations") or ("*",)
    deny = _opt_str_tuple(d, "deny", where=f"{where}.operations")
    return OperationGate(allow=allow, deny=deny)


@dataclass(frozen=True)
class PluginGateSpec:
    """One plugin's level-3 ceiling."""

    plugin_id: str
    core: tuple[str, ...] = ()
    access: str = "read"
    operations: OperationGate = field(default_factory=OperationGate)
    endpoints: tuple[str, ...] = ()
    owner: str = ""

    def permits_core(self, token: str) -> bool:
        """True when holding ``token`` (a ``core:...`` scope) is within this ceiling.

        Empty ``core`` means "no core reach at all" — not "unlimited";
        absence of the whole ``gate`` block (not this field) is what means
        unconstrained, handled one layer up by "no gate registered".
        """
        from core.scope_management.grammar import expand_implied

        held_closure: set[str] = set()
        for t in self.core:
            held_closure |= expand_implied(t)
        return token in held_closure

    def permits_operation(self, operation_id: str) -> bool:
        """True when the plugin's own operation surface allows ``operation_id``."""
        return self.operations.permits(operation_id)

    def permits_endpoint(self, path: str) -> bool:
        """True when ``path`` matches a declared endpoint glob (empty = no HTTP surface declared)."""
        if not self.endpoints:
            return True
        return any(fnmatch.fnmatchcase(path, pat) for pat in self.endpoints)


_ACCESS_VALUES = frozenset({"read", "write"})
_GATE_KEYS = {"core", "access", "operations", "endpoints"}


def parse_gate_spec(plugin_id: str, data: dict[str, Any], *, owner: str = "") -> PluginGateSpec:
    """Parse + strictly validate a plugin gate spec dict. Raises ``GateSpecError``.

    Fail-closed by design: unknown top-level keys reject rather than being
    silently ignored, and a malformed ``core``/``operations``/``endpoints``
    entry rejects the whole spec rather than dropping just that entry.
    """
    if not plugin_id or not isinstance(plugin_id, str):
        raise GateSpecError("plugin gate spec: plugin_id is required")
    if not isinstance(data, dict):
        raise GateSpecError(f"plugin gate spec '{plugin_id}': must be a JSON object")

    unknown = set(data.keys()) - _GATE_KEYS
    if unknown:
        raise GateSpecError(f"plugin gate spec '{plugin_id}': unknown top-level keys {sorted(unknown)}")

    where = f"plugin gate spec '{plugin_id}'"

    access = str(data.get("access", "read")).strip()
    if access not in _ACCESS_VALUES:
        raise GateSpecError(f"{where}: 'access' must be one of {sorted(_ACCESS_VALUES)}")

    raw_core = data.get("core", [])
    if raw_core is None:
        raw_core = []
    if not isinstance(raw_core, list) or not all(isinstance(x, str) and x for x in raw_core):
        raise GateSpecError(f"{where}: 'core' must be a list of non-empty strings")
    core_tokens: list[str] = []
    for tok in raw_core:
        core_tokens.append(tok if ":" in tok else f"core:{tok}:{access}")

    operations = _parse_operations(data.get("operations"), where=where)
    endpoints = _opt_str_tuple(data, "endpoints", where=where)

    return PluginGateSpec(
        plugin_id=plugin_id,
        core=tuple(core_tokens),
        access=access,
        operations=operations,
        endpoints=endpoints,
        owner=owner,
    )


class PluginGateRegistry:
    """Process-level registry of plugin gates: manifest seed + DB override.

    Unlike ``ModelRoleRegistry`` there is no core/plugin precedence to
    arbitrate — just two layers per plugin_id, DB always wins when present,
    and a DB override **fully replaces** (never merges with) the manifest
    gate, matching ``gate_overlay.py``'s documented full-replace semantics.
    Absence of *any* entry for a plugin_id means "no ceiling" (unconstrained,
    today's behaviour) — that is the empty-registry default, not a row you
    have to write.
    """

    def __init__(self) -> None:
        """Initialize empty manifest/DB-override maps."""
        self._lock = threading.Lock()
        self._manifest: dict[str, PluginGateSpec] = {}
        self._db_override: dict[str, PluginGateSpec] = {}

    def register_manifest_gate(self, spec: PluginGateSpec) -> None:
        """Register (or replace) a plugin's manifest-declared gate."""
        with self._lock:
            self._manifest[spec.plugin_id] = spec

    def unregister_manifest_gate(self, plugin_id: str) -> None:
        """Clear a plugin's manifest-declared gate (e.g. on hot-unload)."""
        with self._lock:
            self._manifest.pop(plugin_id, None)

    def set_db_override(self, plugin_id: str, spec: PluginGateSpec | None) -> None:
        """Set (or, when ``spec`` is None, clear) a plugin's DB override."""
        with self._lock:
            if spec is None:
                self._db_override.pop(plugin_id, None)
            else:
                self._db_override[plugin_id] = spec

    def clear_db_overrides(self) -> None:
        """Drop every DB override (manifest gates are untouched)."""
        with self._lock:
            self._db_override.clear()

    def get_gate(self, plugin_id: str) -> PluginGateSpec | None:
        """Effective gate for ``plugin_id``: DB override wins, else manifest, else None."""
        with self._lock:
            if plugin_id in self._db_override:
                return self._db_override[plugin_id]
            return self._manifest.get(plugin_id)

    def all_gated_plugin_ids(self) -> list[str]:
        """Every plugin_id with an effective gate (manifest or DB)."""
        with self._lock:
            return sorted(set(self._manifest) | set(self._db_override))


_registry: PluginGateRegistry | None = None
_registry_lock = threading.Lock()


def get_plugin_gate_registry() -> PluginGateRegistry:
    """Lazy singleton PluginGateRegistry."""
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is None:
            _registry = PluginGateRegistry()
        return _registry


def _set_plugin_gate_registry(reg: PluginGateRegistry | None) -> None:
    """Test hook: replace or clear the singleton."""
    global _registry
    with _registry_lock:
        _registry = reg
