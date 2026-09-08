"""MemoryRegistry — core registry for search/memory vector namespaces.

Plugins register namespaces (declarative manifest ``memory`` or
``PluginContext.contribute_memory``). Core pre-seeds harness/agent collections.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger("whiskers.memory")

Backend = Literal["memory", "search"]

_CORE_OWNER = "core"
_PLUGIN_COLLECTION_RE = re.compile(r"^([a-zA-Z0-9_]+)__(.+)$")

# Bare collection names reserved for core (memory backend).
CORE_COLLECTIONS: frozenset[str] = frozenset(
    {
        "global_memory",
        "plan_recipes",
        "plan_anti_patterns",
        "core_instructions",
    }
)


@dataclass(frozen=True)
class MemoryNamespace:
    """A registered memory or search collection."""

    owner: str
    name: str
    collection: str
    backend: Backend
    description: str = ""
    writable: bool = True
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "name": self.name,
            "collection": self.collection,
            "backend": self.backend,
            "description": self.description,
            "writable": self.writable,
            "tags": list(self.tags),
        }


def plugin_collection(plugin_id: str, name: str) -> str:
    """Build a plugin-scoped physical collection key."""
    safe_name = re.sub(r"[^a-zA-Z0-9_]+", "_", (name or "").strip()).strip("_")
    return f"{plugin_id}__{safe_name}"


def normalize_memory_entries(plugin_id: str, raw: Any) -> list[MemoryNamespace]:
    """Normalize manifest/programmatic memory entries into MemoryNamespace list."""
    if not raw or not isinstance(raw, list):
        return []
    out: list[MemoryNamespace] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        backend = str(item.get("backend") or "memory").strip().lower()
        if backend not in ("memory", "search"):
            backend = "memory"
        collection = str(item.get("collection") or "").strip() or plugin_collection(
            plugin_id, name
        )
        tags_raw = item.get("tags") or []
        tags = tuple(str(t) for t in tags_raw) if isinstance(tags_raw, list) else ()
        out.append(
            MemoryNamespace(
                owner=plugin_id,
                name=name,
                collection=collection,
                backend=backend,  # type: ignore[arg-type]
                description=str(item.get("description") or ""),
                writable=bool(item.get("writable", True)),
                tags=tags,
            )
        )
    return out


class MemoryRegistry:
    """Process-level registry of vector namespaces."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_collection: dict[str, MemoryNamespace] = {}
        self._seed_core_namespaces()

    def _seed_core_namespaces(self) -> None:
        cores = [
            MemoryNamespace(
                owner=_CORE_OWNER,
                name="global",
                collection="global_memory",
                backend="memory",
                description="Agent notes and free-form semantic memory",
            ),
            MemoryNamespace(
                owner=_CORE_OWNER,
                name="plan_recipes",
                collection="plan_recipes",
                backend="memory",
                description="Learned successful plan op chains",
                writable=True,
                tags=("harness",),
            ),
            MemoryNamespace(
                owner=_CORE_OWNER,
                name="plan_anti_patterns",
                collection="plan_anti_patterns",
                backend="memory",
                description="Learned failed plan sequences",
                writable=True,
                tags=("harness",),
            ),
            MemoryNamespace(
                owner=_CORE_OWNER,
                name="core_instructions",
                collection="core_instructions",
                backend="memory",
                description="Optional vector mirror of global harness instructions",
                writable=True,
                tags=("harness", "instructions"),
            ),
        ]
        for ns in cores:
            self._by_collection[ns.collection] = ns

    def _validate_plugin_ns(self, ns: MemoryNamespace) -> str | None:
        """Return error message if namespace is invalid for a non-core owner."""
        if ns.owner == _CORE_OWNER:
            if ns.collection not in CORE_COLLECTIONS:
                return f"core cannot register unknown bare collection {ns.collection!r}"
            return None
        if ns.collection in CORE_COLLECTIONS:
            return f"plugin {ns.owner!r} cannot claim core collection {ns.collection!r}"
        m = _PLUGIN_COLLECTION_RE.match(ns.collection)
        if not m or m.group(1) != ns.owner:
            return (
                f"plugin {ns.owner!r} must use collection prefix "
                f"{ns.owner}__* (got {ns.collection!r})"
            )
        if ns.backend not in ("memory", "search"):
            return f"invalid backend {ns.backend!r}"
        return None

    def register_namespace(
        self, ns: MemoryNamespace, *, replace: bool = False
    ) -> MemoryNamespace:
        """Register a namespace; raise ValueError on squat / invalid."""
        err = self._validate_plugin_ns(ns)
        if err:
            raise ValueError(err)
        with self._lock:
            existing = self._by_collection.get(ns.collection)
            if existing and not replace:
                if existing.owner != ns.owner:
                    raise ValueError(
                        f"collection {ns.collection!r} already owned by {existing.owner!r}"
                    )
                return existing
            self._by_collection[ns.collection] = ns
            logger.debug(
                "memory registry: registered %s backend=%s owner=%s",
                ns.collection,
                ns.backend,
                ns.owner,
            )
            return ns

    def register_entries(
        self, plugin_id: str, raw: Any, *, replace: bool = False
    ) -> list[MemoryNamespace]:
        """Normalize and register a list of raw entries for a plugin."""
        registered: list[MemoryNamespace] = []
        for ns in normalize_memory_entries(plugin_id, raw):
            try:
                registered.append(self.register_namespace(ns, replace=replace))
            except ValueError as exc:
                logger.warning("memory registry: skip %s: %s", plugin_id, exc)
        return registered

    def unregister_owner(self, owner: str) -> int:
        """Remove all namespaces for owner (not core). Returns count removed."""
        if not owner or owner == _CORE_OWNER:
            return 0
        with self._lock:
            to_drop = [
                c for c, ns in self._by_collection.items() if ns.owner == owner
            ]
            for c in to_drop:
                del self._by_collection[c]
            return len(to_drop)

    def get(self, collection: str) -> MemoryNamespace | None:
        """Resolve a physical collection name."""
        with self._lock:
            return self._by_collection.get(collection)

    def list_all(self) -> list[MemoryNamespace]:
        with self._lock:
            return list(self._by_collection.values())

    def list_for_owner(self, owner: str) -> list[MemoryNamespace]:
        with self._lock:
            return [ns for ns in self._by_collection.values() if ns.owner == owner]

    def resolve_backend(self, collection: str) -> Backend | None:
        ns = self.get(collection)
        return ns.backend if ns else None


_REGISTRY: MemoryRegistry | None = None
_REG_LOCK = threading.Lock()


def get_memory_registry() -> MemoryRegistry:
    """Return the process-level MemoryRegistry singleton."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REG_LOCK:
            if _REGISTRY is None:
                _REGISTRY = MemoryRegistry()
    return _REGISTRY


def reset_memory_registry_for_tests() -> MemoryRegistry:
    """Replace the singleton (tests only)."""
    global _REGISTRY
    with _REG_LOCK:
        _REGISTRY = MemoryRegistry()
        return _REGISTRY
