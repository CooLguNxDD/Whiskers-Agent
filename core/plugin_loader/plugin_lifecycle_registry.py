"""
Lifecycle component of the plugin registry for managing phase progression.
"""
import asyncio
import logging
from typing import List, Dict, TYPE_CHECKING
from .types import IPlugin
from .plugin_context import PluginContext
from .skill_registry import clear as clear_plugin_skills, set_plugin_skills as _set_plugin_skills, set_plugin_poll_specs as _set_plugin_poll_specs
from .scope_registry import clear as clear_plugin_scopes, set_plugin_scopes as _set_plugin_scopes

if TYPE_CHECKING:
    from .plugin_registry import PluginRegistry

logger = logging.getLogger("whiskers.plugins")

class PluginLifecycleRegistry:
    """Registry subclass responsible for managing plugin lifecycles."""
    def __init__(self, registry: "PluginRegistry"):
        """Initialize the lifecycle registry with a reference to the main registry."""
        self._registry = registry
        self._plugins: List[IPlugin] = []
        self._plugin_contexts: Dict[str, PluginContext] = {}
        self._plugin_id_map: Dict[str, IPlugin] = {}
        self._plugin_locks: Dict[str, asyncio.Lock] = {}
        self._plugin_tool_counts: Dict[str, int] = {}
        self._loaded = False

    def _get_or_create_context(self, plugin_id: str) -> PluginContext:
        """Get or create a per-plugin PluginContext that persists across phases."""
        if plugin_id not in self._plugin_contexts:
            self._plugin_contexts[plugin_id] = PluginContext(
                self._registry.app,
                self._registry.events,
                self._registry.config,
                plugin_id=plugin_id,
                vault=self._registry._vault,
                relay=self._registry._relay,
                registry=self._registry,
            )
        return self._plugin_contexts[plugin_id]

    def register_plugin(self, plugin: IPlugin) -> None:
        """Add a plugin to the registry. Registration phase strictly happens here."""
        if getattr(plugin, 'min_api_version', 1) > self._registry.API_VERSION:
            logger.warning(f"Plugin {plugin.name} requires API version {plugin.min_api_version}, but host is {self._registry.API_VERSION}. Skipping.")
            return
            
        logger.info(f"Registering plugin: {plugin.name} v{plugin.version} (Tier: {plugin.tier})")
        self._plugins.append(plugin)

        # build plugin name → instance map for quick lookup in lifecycle management
        plugin_name = getattr(plugin, "name", "").strip()
        if not plugin_name:
            logger.warning(f"Skipping plugin with empty name: {plugin}")
            return
        self._plugin_id_map[plugin_name] = plugin
        self._bump_tool_meta_version()

    async def initialize_plugins(self) -> None:
        """Boot up all plugins synchronously via their async lifecycle hooks."""
        if self._loaded:
            return
        
        successfully_loaded_plugins = []

        # Execute on_load for all plugins (persisted per-plugin context)
        for plugin in self._plugins:
            logger.info(f"Loading plugin: {plugin.name}")
            ctx = self._get_or_create_context(getattr(plugin, "name", ""))
            try:
                await plugin.on_load(ctx)
                successfully_loaded_plugins.append(plugin)
            except Exception as e:
                logger.error(f"Failed to load plugin {plugin.name}: {e}")

        # Execute on_ready only for plugins that loaded successfully
        for plugin in successfully_loaded_plugins:
            plugin_id = getattr(plugin, "name", "")
            # Startup direct-token invalidation to force fresh credentials check
            try:
                await self._registry.events.emit_async("plugin.boot", plugin_id)
            except Exception as exc:
                logger.warning("PluginLifecycleRegistry: Failed to clear direct tokens on boot for '%s': %s", plugin_id, exc)

            logger.info(f"Initializing plugin: {plugin_id}")
            ctx = self._get_or_create_context(plugin_id)
            try:
                await plugin.on_ready(ctx)
            except Exception as e:
                logger.error(f"Failed to ready plugin {plugin_id}: {e}")
                
        self._loaded = True
        logger.info("All plugins initialized.")

    @staticmethod
    def _bump_tool_meta_version() -> None:
        """Invalidate core.context.tool_meta's local tool index after any load-set change."""
        try:
            from core.context.tool_meta import bump_version
            bump_version()
        except Exception:
            logger.debug("tool_meta version bump failed (non-fatal)", exc_info=True)

    def _host_cleanup_plugin(self, plugin_id: str) -> None:
        """Host-enforced owner cleanup even when plugin on_unload fails or is partial.

        Clears route registry, operation catalog, skills, and scopes for ``plugin_id``.
        """
        self._bump_tool_meta_version()
        if not plugin_id:
            return
        try:
            if self._registry is not None and getattr(self._registry, "route_registry", None) is not None:
                self._registry.route_registry.remove_plugin(plugin_id)
        except Exception as exc:
            logger.warning("host cleanup: route_registry.remove_plugin(%s) failed: %s", plugin_id, exc)
        try:
            from core.route_registry.operation_catalog import get_operation_catalog
            get_operation_catalog().remove_owner(plugin_id)
        except Exception as exc:
            logger.warning("host cleanup: operation_catalog.remove_owner(%s) failed: %s", plugin_id, exc)
        try:
            clear_plugin_skills(plugin_id)
        except Exception as exc:
            logger.warning("host cleanup: clear_plugin_skills(%s) failed: %s", plugin_id, exc)
        try:
            clear_plugin_scopes(plugin_id)
        except Exception as exc:
            logger.warning("host cleanup: clear_plugin_scopes(%s) failed: %s", plugin_id, exc)
        try:
            from core.memory import get_memory_registry

            get_memory_registry().unregister_owner(plugin_id)
        except Exception as exc:
            logger.warning(
                "host cleanup: memory_registry.unregister_owner(%s) failed: %s",
                plugin_id,
                exc,
            )

    async def teardown_plugins(self) -> None:
        """Gracefully shut down all plugins."""
        for plugin in reversed(self._plugins):
            logger.info(f"Unloading plugin: {plugin.name}")
            name = getattr(plugin, "name", "") or ""
            ctx = self._get_or_create_context(name)
            try:
                await plugin.on_unload(ctx)
            except Exception as e:
                logger.error(f"Error unloading plugin {plugin.name}: {e}")
            # Always host-clean even if on_unload threw before remove_routes.
            self._host_cleanup_plugin(name)

        self._plugins.clear()
        self._plugin_contexts.clear()
        self._plugin_id_map.clear()
        self._plugin_locks.clear()
        self._loaded = False
        clear_plugin_skills()  # clear all skills on full teardown (hot-swap safe)
        clear_plugin_scopes()

    def _get_plugin_lock(self, plugin_id: str) -> asyncio.Lock:
        if plugin_id not in self._plugin_locks:
            self._plugin_locks[plugin_id] = asyncio.Lock()
        return self._plugin_locks[plugin_id]

    def record_tool_count(self, plugin_id: str, count: int) -> None:
        """Record the live tool count for a plugin (updated on each load/unload cycle)."""
        self._plugin_tool_counts[plugin_id] = count

    def get_tool_count(self, plugin_id: str) -> int | None:
        """Return the last recorded tool count for plugin_id, or None if never recorded."""
        return self._plugin_tool_counts.get(plugin_id)

    async def teardown_plugin(self, plugin_id: str) -> None:
        """
        Gracefully tears down a running plugin, including unregistering routes
        and stopping any running background workers or proxy processes.
        """
        from . import resolver

        normalized_id = resolver._normalize_plugin_name(plugin_id)
        if normalized_id.startswith("proxy_"):
            from core.proxy.proxy_manager import proxy_manager
            proxy_name = normalized_id[len("proxy_"):]
            async with self._get_plugin_lock(normalized_id):
                await proxy_manager.disable_proxy(proxy_name)
            return
        async with self._get_plugin_lock(normalized_id):
            plugin = self._plugin_id_map.get(normalized_id)
            if not plugin:
                logger.warning(f"teardown_plugin: Plugin '{plugin_id}' not found in memory.")
                # Still host-clean in case stale contributions remain.
                self._host_cleanup_plugin(normalized_id)
                return
            ctx = self._get_or_create_context(normalized_id)
            try:
                await plugin.on_unload(ctx)
            except Exception as e:
                logger.error(f"Error unloading plugin {plugin_id}: {e}")
            # Host-enforced: clear routes/catalog/skills/scopes even if on_unload failed.
            self._host_cleanup_plugin(normalized_id)

    async def reinitialize_plugin(self, plugin_id: str) -> None:
        """
        Reinitialises an active plugin (tears it down and brings it back up).
        Used during configuration hot-swaps or enablement operations.
        """
        from . import resolver

        normalized_id = resolver._normalize_plugin_name(plugin_id)
        if normalized_id.startswith("proxy_"):
            from core.proxy.proxy_manager import proxy_manager
            proxy_name = normalized_id[len("proxy_"):]
            async with self._get_plugin_lock(normalized_id):
                await proxy_manager.enable_proxy(proxy_name)
            return
        lazy_loaded = False

        async with self._get_plugin_lock(normalized_id):
            plugin = self._plugin_id_map.get(normalized_id)
            if not plugin:
                from .plugin_loader import load_single_plugin
                if await load_single_plugin(self._registry, normalized_id):
                    lazy_loaded = True
                    plugin = self._plugin_id_map.get(normalized_id)

            if not plugin:
                logger.warning(f"reinitialize_plugin: Plugin '{plugin_id}' not found in memory.")
                return

            # Re-resolve skills from current on-disk manifest + prefer DB (user edits survive reinitialize)
            try:
                from . import resolver as _res
                from utils.config_registry import PLUGIN_CONFIG_PATH
                import json
                with open(PLUGIN_CONFIG_PATH, "r", encoding="utf-8") as f:
                    pkgs = json.load(f).get("plugins", [])
                specs, _ = _res.build_specs(pkgs)
                for s in specs:
                    if _res._normalize_plugin_name(s.name) == normalized_id:
                        declared = list(getattr(s, "manifest", {}).get("skills", []) or [])
                        fs_map: dict[str, str] = getattr(s, "skills_map", {}) or {}
                        skill_text = getattr(s, "skills", "") or ""
                        # Prefer DB if available (no full DB registry here, try import)
                        try:
                            from db_layer.plugin_registry_store import DBPluginRegistry as _DBReg
                            _dbr = _DBReg()
                            db_map = await _dbr.get_skills(normalized_id) or {}
                            if db_map:
                                # merge: db first, then missing declared from FS
                                eff = dict(db_map)
                                for k in declared:
                                    if k not in eff and k in fs_map:
                                        eff[k] = fs_map[k]
                                skill_text = "\n\n".join(v for v in eff.values() if v)
                        except Exception:
                            # DB not available or error -> use FS text
                            logger.debug(
                                "reinitialize_plugin: skill DB read failed for %s; using FS skills",
                                normalized_id,
                                exc_info=True,
                            )
                        if skill_text:
                            _set_plugin_skills(normalized_id, skill_text)
                        
                        poll_specs = getattr(s, "manifest", {}).get("poll_specs") or []
                        if poll_specs:
                            _set_plugin_poll_specs(normalized_id, poll_specs)
                        scopes = getattr(s, "manifest", {}).get("scopes") or []
                        if scopes:
                            _set_plugin_scopes(normalized_id, scopes)
                        break
            except Exception as _e:
                logger.debug("reinitialize_plugin: skill refresh skipped (%s)", _e)

            ctx = self._get_or_create_context(normalized_id)
            # Atomic reload: wipe owner contributions before re-contribute so
            # catalog never shows a mixed old+new set for this plugin.
            if not lazy_loaded:
                try:
                    await plugin.on_unload(ctx)
                except Exception as e:
                    logger.error(f"Error on_unload during reinitialize {plugin_id}: {e}")
                self._host_cleanup_plugin(normalized_id)
            try:
                await plugin.on_load(ctx)
            except Exception as e:
                logger.error(f"Error on_load plugin {plugin_id}: {e}")
                # Failed load must not leave partial contributions as the only view.
                self._host_cleanup_plugin(normalized_id)
            if lazy_loaded:
                try:
                    await self._registry.events.emit_async("plugin.boot", normalized_id)
                except Exception as exc:
                    logger.warning(
                        "reinitialize_plugin: Failed to clear direct tokens on boot for '%s': %s",
                        normalized_id, exc,
                    )
            try:
                await plugin.on_ready(ctx)
            except Exception as e:
                logger.error(f"Error on_ready plugin {plugin_id}: {e}")

        if lazy_loaded:
            try:
                from core.context import route_registry
                from core_graph.worker import enqueue_pending
                await enqueue_pending(route_registry)
            except Exception as exc:
                logger.warning("reinitialize_plugin: auto-embed enqueue failed for '%s': %s", plugin_id, exc)
