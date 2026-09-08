"""
Base plugin definition with default lifecycle behavior.

The default ``on_load``/``on_ready``/``on_unload`` implementations cover the
common case shared by every plugin: enable the plugin's tool tag, gate any
persisted-disabled tools, contribute routes to the dynamic-graph RouteRegistry,
register a single-tenant binding, and pre-warm auth. Plugins declare what they
need via class attributes (``module_paths``, ``instance_config``, ``auth_config``,
``auth_delegate``); proxy / dynamic-tool plugins need no lifecycle code at all.
Custom or static plugins override a hook and may call ``super().on_load(ctx)``
to reuse the shared tail.
"""
import logging
from abc import ABC
from typing import List
from .tier import Tier
from .plugin_auth_registry import is_auth_error
from .types import IPluginContext

logger = logging.getLogger("whiskers.plugins")


class Plugin(ABC):
    """
    Base plugin class definition.
    """
    name: str           # e.g. "adaptive_router", "records_agent"
    version: str        # semver
    tier: int = Tier.LITE  # Integer from Tier enum
    min_api_version: int = 1
    requires: List[str] = []  # e.g. ["records@>=1.0.0"]

    # --- Declarative defaults consumed by the default lifecycle hooks ---------
    # Dotted module paths whose import registers @mcp.tool callables. Empty for
    # dynamic plugins that register their tools before on_load runs.
    module_paths: List[str] = []
    # Single-tenant InstanceBinding config (PROJECT_ID/API_URL etc.).
    # None → no default binding is registered.
    instance_config: dict | None = None
    # Direct-login auth config passed to ctx.register_direct_auth_config().
    auth_config: dict | None = None
    # Parent plugin id to delegate auth resolution to (mutually exclusive with
    # auth_config).
    auth_delegate: str | None = None

    def _raw_tool_name(self, operation_id: str) -> str:
        """Strip the ``{plugin}__`` qualifier from a route operation_id."""
        prefix = f"{self.name}__"
        return operation_id[len(prefix):] if operation_id.startswith(prefix) else operation_id

    async def on_load(self, ctx: IPluginContext) -> None:
        """Phase 1: Registration. Enable tools, gate disabled, contribute routes.

        Default behavior:
          1. Wire auth (delegate or direct config) if declared.
          2. Collect this plugin's tools as RouteDescriptors.
          3. Drop tools persisted as disabled (tool_config) — they are neither
             exposed to MCP nor contributed to the route registry, so they are
             never embedded for run_graph either.
          4. Enable the plugin tag, hide the disabled subset, contribute routes.
          5. Register the single-tenant binding and record the tool count.
        """
        # 1. auth wiring
        if self.auth_delegate:
            ctx.delegate_auth_to(self.auth_delegate)
        elif self.auth_config:
            ctx.register_direct_auth_config(self.auth_config)

        from core.proxy_tools.static_tool_loader import collect_from

        # 2 + 3. collect, then gate persisted-disabled tools
        disabled = await ctx.disabled_tool_names(self.name)
        hidden = await ctx.hidden_tool_names(self.name)
        routes = collect_from(self.name, self.module_paths)
        kept = [r for r in routes if self._raw_tool_name(r.operation_id) not in disabled]

        # 4. enable the plugin's tools, then hide the disabled subset
        ctx.enable_tools()
        if disabled:
            ctx.disable_tools(set(disabled))
        if hidden:
            from core.context import tool_visibility
            for n in hidden:
                try:
                    tool_visibility.hide_gateway(n)
                except Exception as exc:
                    logger.warning("Failed to hide gateway tool %s during plugin load: %s", n, exc)
        ctx.contribute_routes(kept)

        # 5. single-tenant binding + tool count
        if self.instance_config is not None:
            from core.route_registry.route_descriptor import InstanceBinding
            ctx.register_binding(InstanceBinding(
                plugin_id=self.name,
                instance_id="default",
                config=self.instance_config,
            ))
        if ctx._registry is not None:
            ctx._registry.lifecycle.record_tool_count(self.name, len(kept))

        logger.info(
            "%s: on_load — contributed %d routes (%d disabled tool(s) gated, %d hidden tool(s) re-hidden)",
            self.name, len(kept), len(disabled), len(hidden),
        )

    async def on_ready(self, ctx: IPluginContext) -> None:
        """Phase 2: Initialization. Best-effort auth pre-warm.

        Failures are non-fatal — auth is retried lazily on the first tool call.

        Reads ``ctx._registry`` directly (same pattern as ``on_load``/``on_unload``)
        rather than the global registry singleton — that is what keeps this
        module free of any import on ``plugin_registry`` (see ``types.py``).
        """
        try:
            if ctx._registry is None:
                logger.warning(
                    "%s: auth pre-warm skipped — no PluginRegistry on context", self.name
                )
                return
            headers = await ctx._registry.auth.get_auth_headers(self.name)
            if headers and not is_auth_error(headers):
                logger.info("%s: auth pre-warmed OK", self.name)
            elif is_auth_error(headers):
                logger.warning("%s: auth pre-warm failed: %s", self.name, headers)
            else:
                logger.warning("%s: auth pre-warm returned no credentials", self.name)
        except Exception as exc:
            logger.warning(
                "%s: auth pre-warm failed (will retry on first tool call): %s",
                self.name, exc,
            )

    async def on_unload(self, ctx: IPluginContext) -> None:
        """Phase 3: Teardown. Hide tools and drop route contributions."""
        ctx.disable_tools()
        ctx.remove_routes()
        if ctx._registry is not None:
            ctx._registry.lifecycle.record_tool_count(self.name, 0)
        logger.info("%s: on_unload — tools disabled, routes removed", self.name)
