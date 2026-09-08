"""Host-enforced plugin cleanup and atomic reinitialize semantics."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.route_registry import RouteRegistry, RouteDescriptor, get_operation_catalog
from core.route_registry.operation_catalog import _reset_operation_catalog_for_tests


def _desc(plugin_id: str, op: str) -> RouteDescriptor:
    return RouteDescriptor(
        plugin_id=plugin_id,
        operation_id=op,
        description="d",
        method="CALL",
        is_fast_path=True,
        tags=(plugin_id, "read"),
    )


@pytest.mark.asyncio
async def test_teardown_cleans_catalog_even_if_on_unload_raises() -> None:
    _reset_operation_catalog_for_tests()
    from core.plugin_loader.plugin_lifecycle_registry import PluginLifecycleRegistry

    reg = MagicMock()
    route_reg = RouteRegistry()
    route_reg.contribute([_desc("p1", "p1__op")])
    reg.route_registry = route_reg

    life = PluginLifecycleRegistry(reg)
    plugin = MagicMock()
    plugin.name = "p1"

    async def boom(_ctx):
        raise RuntimeError("unload failed before remove_routes")

    plugin.on_unload = boom
    life._plugin_id_map["p1"] = plugin
    life._plugins = [plugin]

    await life.teardown_plugin("p1")

    assert route_reg.get("p1__op", "p1") is None
    assert get_operation_catalog().get("p1", "p1__op") is None


@pytest.mark.asyncio
async def test_reinitialize_host_clears_before_reload() -> None:
    _reset_operation_catalog_for_tests()
    from core.plugin_loader.plugin_lifecycle_registry import PluginLifecycleRegistry

    reg = MagicMock()
    route_reg = RouteRegistry()
    route_reg.contribute([_desc("p1", "p1__old")])
    reg.route_registry = route_reg
    reg.events = MagicMock()
    reg.events.emit_async = AsyncMock()

    life = PluginLifecycleRegistry(reg)
    plugin = MagicMock()
    plugin.name = "p1"
    plugin.on_unload = AsyncMock()

    async def on_load(ctx):
        # Simulate re-contribute of a new op only
        route_reg.contribute([_desc("p1", "p1__new")])

    plugin.on_load = on_load
    plugin.on_ready = AsyncMock()
    life._plugin_id_map["p1"] = plugin
    life._plugins = [plugin]

    await life.reinitialize_plugin("p1")

    assert route_reg.get("p1__old", "p1") is None
    assert route_reg.get("p1__new", "p1") is not None
    cat = get_operation_catalog()
    assert cat.get("p1", "p1__old") is None
    assert cat.get("p1", "p1__new") is not None
