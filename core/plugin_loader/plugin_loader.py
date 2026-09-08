"""
Plugin discovery and loading logic for Whiskers Agent.

Handles the dynamic import of plugin packages, manifest parsing,
tier elevation, and registration of tools and services. Delegates
to PluginDiscoveryService, PluginDependencyResolver, and
PluginLifecycleManager for the discrete lifecycle stages.
"""
import asyncio

from core.plugin_loader.plugin_registry import PluginRegistry
from core.plugin_loader.discovery_service import PluginDiscoveryService
from core.plugin_loader.lifecycle_manager import (
    PluginLifecycleManager,
    _relay_manifests,
)

# Exporting these for backward compatibility
parse_tier = PluginDiscoveryService.parse_tier
_normalize_plugin_name = PluginDiscoveryService.normalize_plugin_name
_interpolate_manifest = PluginDiscoveryService.interpolate_manifest

# Aliasing functions to the lifecycle manager for backward compatibility
_db_upsert_and_validate = PluginLifecycleManager.db_upsert_and_validate
_run_plugin_migrations = PluginLifecycleManager.run_plugin_migrations
_set_migration_error_meta = PluginLifecycleManager.set_migration_error_meta
_clear_migration_error_meta = PluginLifecycleManager.clear_migration_error_meta
_persist_spec_content_hash = PluginLifecycleManager.persist_spec_content_hash
_seed_runtime_from_spec = PluginLifecycleManager.seed_runtime_from_spec
_load_one_spec = PluginLifecycleManager.load_one_spec
load_single_plugin = PluginLifecycleManager.load_single_plugin
discover_and_load_plugins_async = PluginLifecycleManager.discover_and_load_plugins_async


def discover_and_load_plugins(registry: PluginRegistry, config_path: str | None = None):
    """Synchronously discover plugins when no event loop is running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(discover_and_load_plugins_async(registry, config_path))
        return

    raise RuntimeError(
        "discover_and_load_plugins() cannot be called from a running event loop. "
        "Use await discover_and_load_plugins_async(...) instead."
    )
