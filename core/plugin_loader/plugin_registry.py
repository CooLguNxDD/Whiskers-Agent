"""
Plugin registry composer for Whiskers Agent.

Imports and re-exports all decomposed classes for clean backwards-compatibility
while maintaining composed life cycle and auth sub-registries.
"""

import logging
from typing import Optional, Dict, Any

from fastmcp import FastMCP

# Re-exports for clean backward compatibility
from .tier import Tier, _tier_display_name
from .plugin import Plugin
from .plugin_event_bus import PluginEventBus, EventBus
from .plugin_context import PluginContext
from .auth_status import AuthStatus
from .plugin_lifecycle_registry import PluginLifecycleRegistry
from .plugin_auth_registry import PluginAuthRegistry

logger = logging.getLogger("whiskers.plugins")

class PluginRegistry:
    """
    Central composing registry for managing Whiskers Agent plugins.
    
    Coordinates lifecycle and authentication operations by delegating to
    composed sub-registries.
    """
    API_VERSION = 2

    def __init__(
        self,
        app: FastMCP,
        config: Optional[dict] = None,
        *,
        vault: Any = None,
        relay: Any = None,
        route_registry: Any = None,
        http_route_registry: Any = None,
    ):
        """Initialize the central plugin registry and compose sub-registries."""
        self.app = app
        self.events = PluginEventBus()
        self.system_tier: int = Tier.LITE
        self.config = config or {}
        self._vault = vault
        self._relay = relay
        self._services: Dict[str, Any] = {}

        if route_registry is None or http_route_registry is None:
            from core.context import route_registry as _route_registry
            from core.context import http_route_registry as _http_route_registry
            route_registry = route_registry if route_registry is not None else _route_registry
            http_route_registry = (
                http_route_registry if http_route_registry is not None else _http_route_registry
            )
        self.route_registry = route_registry
        self.http_route_registry = http_route_registry
        
        # Shared context (no plugin_id)
        self.context = PluginContext(
            self.app,
            self.events,
            self.config,
            plugin_id="",
            vault=vault,
            relay=relay,
            registry=self,
        )
        
        # Composed sub-registries
        self.lifecycle = PluginLifecycleRegistry(self)
        self.auth = PluginAuthRegistry(self)
        self._wire_events()

    def register_service(self, service_name: str, service: Any) -> None:
        """Publish a shared object other plugins can locate without importing us."""
        self._services[service_name] = service

    def get_service(self, service_name: str) -> Any:
        """Look up a service published by another plugin, or None when absent."""
        return self._services.get(service_name)

    def _wire_events(self) -> None:
        """Subscribe manager-side handlers for plugin lifecycle events.

        Dispatches through ``self.route_registry`` (constructor-injected or the
        global singleton) rather than a module-level import, so tests and
        callers can supply a stand-in route registry.
        """
        self.events.on("tools.enable", self._on_tools_enable)
        self.events.on("tools.disable", self._on_tools_disable)
        self.events.on("routes.contribute", self._on_routes_contribute)
        self.events.on("routes.bind", self._on_routes_bind)
        self.events.on("routes.remove", self._on_routes_remove)
        self.events.on("scopes.contribute", self._on_scopes_contribute)
        self.events.on("memory.contribute", self._on_memory_contribute)
        self.events.on("subgraphs.contribute", self._on_subgraphs_contribute)
        self.events.on("flow_specs.contribute", self._on_flow_specs_contribute)

    def _on_tools_enable(self, plugin_id: str) -> None:
        self.app.enable(tags={plugin_id})

    def _on_routes_contribute(self, plugin_id: str, routes: list) -> None:
        self.route_registry.contribute(routes)

    def _on_routes_bind(self, plugin_id: str, binding: Any) -> None:
        self.route_registry.register_binding(binding)

    def _on_routes_remove(self, plugin_id: str) -> None:
        self.route_registry.remove_plugin(plugin_id)

    def _on_scopes_contribute(self, plugin_id: str, scopes: list) -> None:
        from core.scope_management.registration import register_plugin_permissions
        register_plugin_permissions(plugin_id, scopes, replace=False)

    def _on_memory_contribute(self, plugin_id: str, entries: list) -> None:
        from core.memory import get_memory_registry

        get_memory_registry().register_entries(plugin_id, entries, replace=False)

    def _on_subgraphs_contribute(self, plugin_id: str, spec: Any) -> None:
        """Register a plugin-contributed subgraph / domain agent."""
        from core_graph.subgraphs.registry import coerce_subgraph_spec, register_subgraph

        try:
            subgraph = coerce_subgraph_spec(spec, plugin_id=plugin_id)
            register_subgraph(subgraph)
        except Exception as exc:
            logger.warning(
                "subgraphs.contribute failed for plugin %s: %s", plugin_id, exc
            )

    def _on_flow_specs_contribute(self, plugin_id: str, spec: Any) -> None:
        """Register a plugin-contributed FlowSpec (runtime ``contribute_flow_spec`` path)."""
        from core_graph.subgraphs.specialist.flow_registry import register_flow
        from core_graph.subgraphs.specialist.flow_spec import FlowSpec, parse_flow_spec

        try:
            flow = spec if isinstance(spec, FlowSpec) else parse_flow_spec(spec, owner=plugin_id)
            if flow.owner != plugin_id:
                flow = FlowSpec(**{**flow.__dict__, "owner": plugin_id})
            register_flow(flow)
        except Exception as exc:
            logger.warning(
                "flow_specs.contribute failed for plugin %s: %s", plugin_id, exc
            )

    def _on_tools_disable(
        self, plugin_id: str, *, names: set[str] | None = None, by_tag: bool = False
    ) -> None:
        if by_tag:
            self.app.disable(tags={plugin_id})
        elif names:
            self.app.disable(names=names, components={"tool"})

    def elevate_tier(self, tier: int) -> None:
        """Tier builder mechanism. Allows the system to transition to a higher tier."""
        if tier > self.system_tier:
            self.system_tier = tier
            self.config["system_tier"] = tier
            logger.info(f"System tier has been elevated to {_tier_display_name(tier)}.")

    async def get_auth_headers(self, plugin_id: str, _visited: Optional[set] = None) -> dict:
        """Forward get_auth_headers to the composed auth sub-registry."""
        return await self.auth.get_auth_headers(plugin_id, _visited)

    async def refresh_auth_headers(self, plugin_id: str) -> dict:
        """Forward refresh_auth_headers to the composed auth sub-registry."""
        return await self.auth.refresh_auth_headers(plugin_id)

    def mark_needs_reauth(self, plugin_id: str) -> None:
        """Forward mark_needs_reauth to the composed auth sub-registry."""
        self.auth.mark_needs_reauth(plugin_id)


_registry_instance: Optional[PluginRegistry] = None

def get_registry() -> PluginRegistry:
    """Returns the singleton instance of PluginRegistry."""
    if _registry_instance is None:
        raise RuntimeError("PluginRegistry not initialized — server not started yet")
    return _registry_instance

def _set_registry(r: PluginRegistry) -> None:
    global _registry_instance
    _registry_instance = r
