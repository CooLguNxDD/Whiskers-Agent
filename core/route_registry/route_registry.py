"""
RouteRegistry — singleton bus that all plugin route contributions land in.

Instantiated once in ``core.context`` next to ``mcp``, ``vault``, and
``oauth_relay``. Plugins call ``contribute()`` from their ``on_load`` (or
``register()``) with a batch of RouteDescriptors collected via either
``static_tool_loader`` or ``dynamic_route_loader``, then call
``register_binding()`` once per instance/org they want the routes to be
callable under (at minimum the implicit ``"default"`` binding).

Two-layer model:
  * Route (shape)   — keyed by ``(plugin_id, operation_id)``. Description,
    parameters, default callable. One row per tool, shared across orgs.
  * Instance binding — keyed by ``(plugin_id, instance_id)``. Per-org config
    dict + optional callable override. Resolved by the executor at call time.

Reads:
  * embedder_node → ``all_routes()`` is NOT used (embedder reads from
    ``route_embeddings`` directly via ``search_routes``).
  * executor_node (fast-path) → ``fast_path_callable(op_id, plugin_id,
    instance_id)`` + ``get_binding(plugin_id, instance_id)``.
  * job_producer → ``all_routes()`` to diff against ``route_embeddings`` and
    enqueue embedding work.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Iterable, Iterator

from core.route_registry.route_descriptor import InstanceBinding, RouteDescriptor

logger = logging.getLogger("whiskers")


from core.interfaces import IRouteRegistry

class RouteRegistry(IRouteRegistry):
    """In-memory registry of all routes contributed by plugins.

    Thread-safe writes (plugins may load in parallel); lock-free reads.
    """

    def __init__(self) -> None:
        """Initialize the route registry with empty routes and bindings dictionary."""
        self._routes: dict[tuple[str, str], RouteDescriptor] = {}
        self._bindings: dict[tuple[str, str], InstanceBinding] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Clear all routes and bindings (and mirror live OperationCatalog)."""
        with self._lock:
            self._routes.clear()
            self._bindings.clear()
        from core.route_registry.operation_catalog import get_operation_catalog
        get_operation_catalog().clear()


    # -- Writes --------------------------------------------------------------

    def contribute(self, routes: Iterable[RouteDescriptor]) -> int:
        """Register a batch of routes for a plugin.

        Returns the number of new/updated routes. Re-registering the same
        ``(plugin_id, operation_id)`` overwrites the prior descriptor (last
        write wins) — this keeps re-init / hot-reload working. Cross-plugin
        collisions (same key contributed by a different plugin's tags) log a
        WARNING naming both source tag sets so the operator can spot it.

        After the registry write, each affected plugin's full route set is
        re-published atomically into the live OperationCatalog.
        """
        added = 0
        routes_list = list(routes)
        with self._lock:
            for r in routes_list:
                if not r.plugin_id or not r.operation_id:
                    logger.warning(
                        "RouteRegistry: skipping descriptor with empty key: %r", r
                    )
                    continue
                prior = self._routes.get(r.key)
                if prior is not None and prior.tags != r.tags:
                    logger.warning(
                        "RouteRegistry: collision on %s — overwriting "
                        "(prior tags=%s, new tags=%s); last-write-wins",
                        r.key, prior.tags, r.tags,
                    )
                self._routes[r.key] = r
                added += 1

        if routes_list:
            plugin_ids = {r.plugin_id for r in routes_list if r.plugin_id}
            for pid in plugin_ids:
                count = sum(1 for r in routes_list if r.plugin_id == pid)
                fast_count = sum(
                    1 for r in routes_list
                    if r.plugin_id == pid and r.is_fast_path
                )
                logger.info(
                    "RouteRegistry: %s contributed %d routes (fast_path=%d)",
                    pid, count, fast_count,
                )
            # Mirror full owner sets into the live catalog (atomic per owner),
            # merging with host HTTP ops that share the same plugin_id.
            from core.route_registry.host_catalog import publish_owner_merged
            for pid in plugin_ids:
                ops = [r.to_operation() for r in self.routes_for_plugin(pid)]
                try:
                    publish_owner_merged(pid, ops)
                except ValueError as exc:
                    logger.warning(
                        "RouteRegistry: catalog publish failed for %s: %s", pid, exc,
                    )
        return added

    def register_binding(self, binding: InstanceBinding) -> None:
        """Register (or overwrite) a per-instance binding for a plugin.

        Every plugin must register at least the implicit ``"default"`` binding
        for its tools to be callable in single-tenant deployments. Multi-org
        plugins call this once per org with the appropriate config dict.
        """
        if not binding.plugin_id or not binding.instance_id:
            logger.warning(
                "RouteRegistry: skipping binding with empty key: %r", binding
            )
            return
        with self._lock:
            prior = self._bindings.get(binding.key)
            self._bindings[binding.key] = binding
        verb = "updated" if prior is not None else "registered"
        logger.info(
            "RouteRegistry: %s binding for plugin=%s instance=%s "
            "(config_keys=%s, override=%s)",
            verb, binding.plugin_id, binding.instance_id,
            sorted(binding.config.keys()),
            binding.callable_override is not None,
        )

    def remove_plugin(self, plugin_id: str) -> int:
        """Drop every route + binding owned by ``plugin_id``. Used by hot-unload."""
        removed = 0
        with self._lock:
            route_keys = [k for k in self._routes if k[0] == plugin_id]
            for k in route_keys:
                del self._routes[k]
                removed += 1
            binding_keys = [k for k in self._bindings if k[0] == plugin_id]
            for k in binding_keys:
                del self._bindings[k]
        # Drop plugin tool ops but keep host HTTP-mirrored ops for the same owner
        # (e.g. terminal control plane under cat_terminal_relay_plugin).
        from core.route_registry.host_catalog import host_ops_for_owner
        from core.route_registry.operation_catalog import get_operation_catalog
        host = host_ops_for_owner(plugin_id)
        catalog = get_operation_catalog()
        if host:
            catalog.publish_owner(plugin_id, host)
        else:
            catalog.remove_owner(plugin_id)
        return removed

    # -- Reads ---------------------------------------------------------------

    def all_routes(self) -> list[RouteDescriptor]:
        """Return all registered route descriptors."""
        return list(self._routes.values())

    def routes_for_plugin(self, plugin_id: str) -> list[RouteDescriptor]:
        """Return all registered route descriptors for a specific plugin."""
        return [r for r in self._routes.values() if r.plugin_id == plugin_id]

    def get(self, operation_id: str,
            plugin_id: str | None = None) -> RouteDescriptor | None:
        """Find a route descriptor by operation ID, optionally scoped to a plugin."""
        if plugin_id:
            return self._routes.get((plugin_id, operation_id))
        # Search across plugins; first match wins (deterministic via dict order).
        for (_pid, op), desc in self._routes.items():
            if op == operation_id:
                return desc
        return None

    def get_binding(
        self, plugin_id: str, instance_id: str = "default",
    ) -> InstanceBinding | None:
        """Return the binding for ``(plugin_id, instance_id)`` or None."""
        return self._bindings.get((plugin_id, instance_id))

    def bindings_for_plugin(self, plugin_id: str) -> list[InstanceBinding]:
        """Return all registered instance bindings for a specific plugin."""
        return [b for b in self._bindings.values() if b.plugin_id == plugin_id]

    def fast_path_callable(
        self,
        operation_id: str,
        plugin_id: str | None = None,
        instance_id: str = "default",
    ) -> Callable[..., Any] | None:
        """Resolve the callable to invoke for a fast-path route.

        ``plugin_id`` is REQUIRED. Calling without it returns ``None`` and
        logs a warning — there is no longer an op-only index because
        first-write-wins masked real cross-plugin collisions.

        Resolution order:
          1. ``InstanceBinding.callable_override`` if the binding sets one.
          2. ``RouteDescriptor.callable_ref`` (the plugin's default callable).
        """
        if not plugin_id:
            logger.warning(
                "RouteRegistry.fast_path_callable(%r) called without plugin_id "
                "— refusing to disambiguate. Pass plugin_id from the route metadata.",
                operation_id,
            )
            return None

        desc = self._routes.get((plugin_id, operation_id))
        if desc is None or not desc.is_fast_path:
            return None

        binding = self._bindings.get((plugin_id, instance_id))
        if binding is not None and binding.callable_override is not None:
            return binding.callable_override
        return desc.callable_ref

    def __iter__(self) -> Iterator[RouteDescriptor]:
        return iter(self._routes.values())

    def __len__(self) -> int:
        return len(self._routes)

    def stats(self) -> dict[str, Any]:
        """Counters useful for startup logs."""
        plugins: dict[str, int] = {}
        fast = 0
        for r in self._routes.values():
            plugins[r.plugin_id] = plugins.get(r.plugin_id, 0) + 1
            if r.is_fast_path:
                fast += 1
        bindings_per_plugin: dict[str, int] = {}
        for b in self._bindings.values():
            bindings_per_plugin[b.plugin_id] = (
                bindings_per_plugin.get(b.plugin_id, 0) + 1
            )
        return {
            "total": len(self._routes),
            "fast_path": fast,
            "plugins": plugins,
            "bindings": bindings_per_plugin,
        }
