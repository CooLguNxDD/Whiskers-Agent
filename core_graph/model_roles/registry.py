"""Process-wide registry of ModelRoleSpecs — core defaults, plugin contributions, DB overrides.

Mirrors ``core_graph.subgraphs.specialist.flow_registry.FlowRegistry`` (dict
keyed by id, owner-scoped bulk unregister, module-level free functions,
process-wide singleton) with one deliberate divergence: **no catalog sync** —
model roles are not dispatchable GOAP operations, just a resolution table
consulted by ``core_graph.model_roles.ladder``.

Precedence when the same ``role_id`` is defined in more than one place is
**DB override > core default > plugin** — the opposite of ``FlowRegistry``'s
last-write-wins. A plugin attempting to register a core-owned role_id (e.g.
``triage``) is rejected with a warning, not merged: plugins may *add* new
roles, never silently retune ``triage``/``summary``/etc. for every tenant.
The DB layer (an operator's explicit intent, see ``db_layer.model_role_store``)
is applied as an overlay on top of the in-memory registry rather than a
registry mutation, so ``unregister_owner`` and plugin hot-reload stay simple.
"""

from __future__ import annotations

import logging
import threading

from core_graph.model_roles.role_spec import ModelRoleSpec

logger = logging.getLogger("whiskers.core_graph.model_roles.registry")

_CORE_OWNER = "core"


class ModelRoleRegistry:
    """In-memory map of role_id -> ModelRoleSpec, split into core/plugin buckets + a DB overlay."""

    def __init__(self) -> None:
        self._core: dict[str, ModelRoleSpec] = {}
        self._plugin: dict[str, ModelRoleSpec] = {}
        self._db_overlay: dict[str, ModelRoleSpec] = {}

    def register(self, spec: ModelRoleSpec) -> ModelRoleSpec:
        """Add or replace a role spec.

        Specs owned by ``"core"`` go into the core bucket (last-write-wins,
        matching FlowRegistry, since core specs are trusted). A plugin spec
        whose ``role_id`` already exists in the core bucket is rejected —
        logged and dropped, not raised, so one misbehaving plugin manifest
        cannot abort plugin load.
        """
        if not isinstance(spec, ModelRoleSpec):
            raise TypeError("Only ModelRoleSpec instances can be registered.")
        if not spec.role_id:
            raise ValueError("ModelRoleSpec.role_id is required")

        if spec.owner == _CORE_OWNER:
            self._core[spec.role_id] = spec
            logger.debug("core model role registered: %s", spec.role_id)
            return spec

        if spec.role_id in self._core:
            logger.warning(
                "model role '%s' from plugin owner=%s rejected: shadows a core role",
                spec.role_id,
                spec.owner,
            )
            return self._core[spec.role_id]

        self._plugin[spec.role_id] = spec
        logger.info("model role registered: %s (owner=%s)", spec.role_id, spec.owner)
        return spec

    def get(self, role_id: str) -> ModelRoleSpec | None:
        """Effective spec for ``role_id``: DB override > core default > plugin."""
        if role_id in self._db_overlay:
            return self._db_overlay[role_id]
        if role_id in self._core:
            return self._core[role_id]
        return self._plugin.get(role_id)

    def source_of(self, role_id: str) -> str | None:
        """Which tier the effective spec for ``role_id`` came from, or None if unregistered."""
        if role_id in self._db_overlay:
            return "db"
        if role_id in self._core:
            return "core"
        if role_id in self._plugin:
            return "plugin"
        return None

    def list_roles(self, *, owner: str | None = None) -> list[ModelRoleSpec]:
        """Effective specs for every known role_id (sorted), optionally filtered by owner."""
        ids = sorted(set(self._core) | set(self._plugin) | set(self._db_overlay))
        roles = [self.get(rid) for rid in ids]
        roles = [r for r in roles if r is not None]
        if owner is None:
            return roles
        return [r for r in roles if r.owner == owner]

    def unregister(self, role_id: str) -> bool:
        """Remove a plugin-owned role by id. Core roles are not removable this way."""
        return self._plugin.pop(role_id, None) is not None

    def unregister_owner(self, owner: str) -> int:
        """Remove every plugin role contributed by ``owner`` (plugin unload/hot-swap)."""
        dead = [rid for rid, r in self._plugin.items() if r.owner == owner]
        for rid in dead:
            self._plugin.pop(rid, None)
        return len(dead)

    def set_db_override(self, role_id: str, spec: ModelRoleSpec | None) -> None:
        """Set (or, when ``spec`` is None, clear) the DB overlay for ``role_id``."""
        if spec is None:
            self._db_overlay.pop(role_id, None)
        else:
            self._db_overlay[role_id] = spec

    def clear_db_overrides(self) -> None:
        """Drop every DB overlay entry."""
        self._db_overlay.clear()

    def clear(self) -> None:
        """Remove all registrations, including DB overlays (tests only)."""
        self._core.clear()
        self._plugin.clear()
        self._db_overlay.clear()


_REGISTRY: ModelRoleRegistry | None = None
_registry_lock = threading.Lock()
_seeded = False
_seed_lock = threading.Lock()


def _ensure_registry() -> ModelRoleRegistry:
    """Registry instance without triggering built-in seeding (used *during* seeding)."""
    global _REGISTRY
    if _REGISTRY is None:
        with _registry_lock:
            if _REGISTRY is None:
                _REGISTRY = ModelRoleRegistry()
    return _REGISTRY


def _seed_builtin_roles() -> None:
    """Load core_roles.json into the registry once (idempotent, recursion-safe)."""
    global _seeded
    if _seeded:
        return
    with _seed_lock:
        if _seeded:
            return
        from core_graph.model_roles.builtin import load_builtin_roles

        load_builtin_roles()
        _seeded = True


def get_model_role_registry() -> ModelRoleRegistry:
    """Process-wide ModelRoleRegistry singleton, seeded with core defaults on first access."""
    reg = _ensure_registry()
    _seed_builtin_roles()
    return reg


def register_model_role(spec: ModelRoleSpec) -> ModelRoleSpec:
    """Register or replace a model role on the global registry."""
    return _ensure_registry().register(spec)


def get_model_role(role_id: str) -> ModelRoleSpec | None:
    """Look up the effective spec for ``role_id`` on the global registry."""
    return get_model_role_registry().get(role_id)


def list_model_roles(*, owner: str | None = None) -> list[ModelRoleSpec]:
    """List all effective role specs (sorted by role_id), optionally by owner."""
    return get_model_role_registry().list_roles(owner=owner)


def unregister_model_role(role_id: str) -> bool:
    """Unregister a plugin-owned role by id from the global registry."""
    return get_model_role_registry().unregister(role_id)


def unregister_model_role_owner(owner: str) -> int:
    """Unregister every model role contributed by ``owner``. Returns count removed."""
    return get_model_role_registry().unregister_owner(owner)


def clear_model_roles() -> None:
    """Clear the global registry (tests only)."""
    get_model_role_registry().clear()


def _reset_model_roles_for_tests() -> None:
    """Reset the singleton and seed flag so the next access reseeds cleanly (tests only)."""
    global _REGISTRY, _seeded
    with _registry_lock:
        _REGISTRY = None
    with _seed_lock:
        _seeded = False
