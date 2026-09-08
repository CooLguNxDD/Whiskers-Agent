"""
Plugin service locator containing utility tools, routing, and vaults context.
"""
import logging
from typing import Callable, Any, Dict, TYPE_CHECKING, Awaitable
from fastmcp import FastMCP

if TYPE_CHECKING:
    from db_layer.vault import VaultService
    from oauth.oauth_relay import ExternalOAuthRelay
    from .plugin_registry import PluginRegistry
    from .plugin_event_bus import PluginEventBus

logger = logging.getLogger("whiskers.plugins")

class PluginContext:
    """The sandbox provided to every plugin (Service Locator).

    Gains ``_plugin_id``, ``_vault``, and ``_relay`` so plugins can access
    Layer 2 credentials and external OAuth tokens via:
      - ``await ctx.get_credential(key_name)``
      - ``await ctx.get_external_token(provider)``

    ``artifact_store`` / ``auth_service`` are the plugin-facing boundary over
    ``core.artifact_store`` / inbound-auth internals (``IArtifactStore`` /
    ``IAuthService`` in ``core/interfaces/``) — plugins must go through these,
    never import ``core.artifact_store.minio_client``, ``db_layer.artifact_link_store``,
    ``db_layer.api_key_store``, or ``oauth_provider._svc`` directly. Many call
    sites (route handlers, ``@mcp.tool()`` functions, pre-accept WS handshakes)
    never receive a ``PluginContext`` at all — those import the same singletons
    via ``core.artifact_store.get_artifact_store()`` / ``core.auth_service.get_auth_service()``.
    """
    def __init__(
        self,
        app: FastMCP,
        events: "PluginEventBus",
        config: dict,
        *,
        plugin_id: str = "",
        vault: "VaultService | None" = None,
        relay: "ExternalOAuthRelay | None" = None,
        registry: "PluginRegistry | None" = None,
    ):
        """Initialize the PluginContext service locator wrapper for a plugin."""
        self._app = app
        self.events = events
        self.config = config
        self._plugin_id = plugin_id
        self._vault = vault
        self._relay = relay
        self._registry = registry
        self._services: Dict[str, Any] = {}

    @property
    def artifact_store(self) -> Any:
        """The plugin-facing ``IArtifactStore`` — object storage + short_id links.

        Resolved lazily (not constructor-injected) since it is a process-wide
        singleton identical for every plugin, same as the module-level
        ``get_artifact_store()`` route/tool handlers call directly.
        """
        from core.artifact_store import get_artifact_store

        return get_artifact_store()

    @property
    def auth_service(self) -> Any:
        """The plugin-facing ``IAuthService`` — bearer/cookie -> principal, token minting.

        Resolved lazily for the same reason as ``artifact_store``.
        """
        from core.auth_service import get_auth_service

        return get_auth_service()

    def register_tool(self, name: str, fn: Callable):
        """Wraps the FastMCP tool registration."""
        self._app.tool(name=name)(fn)

    def get_app(self) -> FastMCP:
        """Expose raw app if strictly necessary, prefer abstractions."""
        return self._app

    def register_service(self, service_name: str, service: Any):
        """Register a shared service for other plugins to locate."""
        if self._registry is not None:
            self._registry.register_service(service_name, service)
        else:
            # Fallback for base context or direct usage without registry
            self._services[service_name] = service

    def get_service(self, service_name: str) -> Any:
        """Service locator pattern for plugins to share data without importing."""
        if self._registry is not None:
            return self._registry.get_service(service_name)
        return self._services.get(service_name)

    def register_auth(self, fn: "Callable[[], Awaitable[dict[str, str]]]") -> None:
        """Register the auth header provider function for this plugin."""
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        self._registry.auth.register_auth(self._plugin_id, fn)

    async def disabled_tool_names(self, plugin_id: str | None = None) -> set[str]:
        """Return raw tool names persisted as disabled for ``plugin_id``.

        Reads the ``tool_config`` table (sparse — only disabled tools have a
        row). Used by the default ``on_load`` to gate per-tool exposure + route
        contribution at load time. Returns an empty set when the DB is
        unavailable so plugins still load with everything enabled.
        """
        pid = plugin_id or self._plugin_id
        if not pid:
            return set()
        try:
            from core.context import _DB_AVAILABLE
            if not _DB_AVAILABLE:
                return set()
            from db_layer.tool_config_store import get_tool_states
            states = await get_tool_states(pid)
            return {name for name, enabled in states.items() if not enabled}
        except Exception as exc:
            logger.warning(
                "PluginContext.disabled_tool_names(%s) failed (treating none disabled): %s",
                pid, exc,
            )
            return set()

    async def hidden_tool_names(self, plugin_id: str | None = None) -> set[str]:
        """Return raw tool names persisted as hidden for ``plugin_id``.

        Reads the ``tool_config`` table (sparse — only hidden tools have a
        row with is_hidden=True). Used by the default ``on_load`` to re-hide
        gateway hidden tools at load/hot-swap time. Returns an empty set when
        the DB is unavailable.
        """
        pid = plugin_id or self._plugin_id
        if not pid:
            return set()
        try:
            from core.context import _DB_AVAILABLE
            if not _DB_AVAILABLE:
                return set()
            from db_layer.tool_config_store import get_tool_hidden_states
            states = await get_tool_hidden_states(pid)
            return {name for name, hidden in states.items() if hidden}
        except Exception as exc:
            logger.warning(
                "PluginContext.hidden_tool_names(%s) failed (treating none hidden): %s",
                pid, exc,
            )
            return set()


    def delegate_auth_to(self, parent_plugin_id: str) -> None:
        """Declare that this plugin resolves auth via a parent plugin."""
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        self._registry.auth.set_auth_delegate(self._plugin_id, parent_plugin_id)

    def register_direct_auth_config(self, config: dict) -> None:
        """Register a standard direct-login config; registry owns the resolution logic.

        config keys: login_url, username, password, auth_header, provider,
                     base_headers, layer2_oauth_enabled (bool), default_ttl (int seconds).
        """
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        self._registry.auth.register_direct_auth(self._plugin_id, config)

    def enable_tools(self) -> None:
        """Enable this plugin's tools via the registry event bus."""
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        self.events.emit("tools.enable", self._plugin_id)

    def disable_tools(self, names: set[str] | None = None) -> None:
        """Disable tools by name or by plugin tag when names is omitted."""
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        if names:
            self.events.emit("tools.disable", self._plugin_id, names=names)
        else:
            self.events.emit("tools.disable", self._plugin_id, by_tag=True)

    def contribute_routes(self, routes: list) -> None:
        """Contribute route descriptors to the dynamic route registry."""
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        self.events.emit("routes.contribute", self._plugin_id, routes)

    def register_binding(self, binding: Any) -> None:
        """Register a single-tenant instance binding for this plugin."""
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        self.events.emit("routes.bind", self._plugin_id, binding)

    def remove_routes(self) -> None:
        """Remove all route contributions for this plugin."""
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        self.events.emit("routes.remove", self._plugin_id)

    def contribute_scopes(self, scopes: list) -> None:
        """Programmatically register requested scope permission tokens.

        Merges with (does not replace) any scopes already declared in the
        plugin's manifest — call from ``on_load`` for scopes that can only
        be known at runtime. Each entry is a scope-token string or
        ``{"token", "description", "access"}`` dict; anti-squatting still
        applies (only ``plugin:<own-id>`` / ``group:<own-id>:<tag>`` allowed).
        """
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        self.events.emit("scopes.contribute", self._plugin_id, scopes)

    def contribute_memory(self, entries: list) -> None:
        """Register plugin memory/search namespaces into the core MemoryRegistry.

        Each entry is a dict: ``{name, backend, description?, writable?, tags?,
        collection?}``. Physical collection defaults to ``{plugin_id}__{name}``.
        Call from ``on_load`` for runtime namespaces; merges with manifest
        ``"memory"`` entries.
        """
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        self.events.emit("memory.contribute", self._plugin_id, entries)

    def contribute_subgraph(self, spec: Any) -> None:
        """Register a subgraph or domain agent into the SubgraphRegistry.

        ``spec`` is a ``SubgraphSpec`` or a dict with at least ``id`` and
        ``name`` (optional ``handler``, ``graph_spec``, fragment nodes/edges,
        ``metadata``). Emits ``subgraphs.contribute`` so the host registry
        stamps ``plugin_id`` and registers the entry.
        """
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        self.events.emit("subgraphs.contribute", self._plugin_id, spec)

    def contribute_flow_spec(self, spec: Any) -> None:
        """Register a declarative specialist FlowSpec at runtime.

        ``spec`` is a ``core_graph.subgraphs.specialist.flow_spec.FlowSpec`` or
        a raw dict accepted by ``parse_flow_spec``. Most flows should be
        declared as JSON files under ``flow_specs/`` and linked from
        ``manifest.json`` (``settings.specialist_agent.flow_specs``) instead —
        this method is for flows that can only be built at runtime. Emits
        ``flow_specs.contribute`` so the host registry stamps ``owner`` and
        registers the entry.
        """
        if self._registry is None:
            raise RuntimeError("No PluginRegistry on this context")
        self.events.emit("flow_specs.contribute", self._plugin_id, spec)

    # -- Layer 2 credential + token access -----------------------------------

    async def get_credential(self, key_name: str) -> str:
        """Return the decrypted vault credential for this plugin.

        Raises ``MissingCredentialError`` with an actionable fix instruction
        if the key is absent.
        """
        if self._vault is None:
            raise RuntimeError(
                "VaultService is not available — ensure DATABASE_URL and "
                "MASTER_KEY are set."
            )
        return await self._vault.get_required(self._plugin_id, key_name)

    async def get_external_token(
        self, provider: str, *, force_refresh: bool = False
    ) -> str:
        """Return a valid external OAuth access token for ``provider``.

        Auto-refreshes if the token expires within 60 seconds.
        Raises if no token is stored (OAuth flow not yet completed) or if
        the relay is not configured.
        """
        if self._relay is None:
            raise RuntimeError(
                "ExternalOAuthRelay is not available — ensure DATABASE_URL "
                "and MASTER_KEY are set."
            )
        return await self._relay.get_token(
            self._plugin_id, provider, force_refresh=force_refresh
        )
