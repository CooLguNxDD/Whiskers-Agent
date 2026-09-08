"""Unit tests for plugin lifecycle decoupling via PluginEventBus."""

from unittest.mock import AsyncMock, patch

import pytest

from core.plugin_loader.plugin import Plugin
from core.plugin_loader.plugin_registry import PluginRegistry, _set_registry
from core.route_registry.route_descriptor import RouteDescriptor


class _StubMCP:
    """Minimal FastMCP stand-in recording enable/disable calls."""

    def __init__(self) -> None:
        self.enabled_tags: set[str] = set()
        self.disabled_tags: set[str] = set()
        self.disabled_names: set[str] = set()

    def enable(self, *, tags: set[str] | None = None, **kwargs) -> None:
        if tags:
            self.enabled_tags.update(tags)

    def disable(
        self,
        *,
        tags: set[str] | None = None,
        names: set[str] | None = None,
        components: dict | None = None,
        **kwargs,
    ) -> None:
        if tags:
            self.disabled_tags.update(tags)
        if names:
            self.disabled_names.update(names)


class _StubRouteRegistry:
    """Minimal route registry stand-in recording lifecycle mutations."""

    def __init__(self) -> None:
        self.contributed: list[list] = []
        self.bindings: list = []
        self.removed: list[str] = []

    def contribute(self, routes: list) -> None:
        self.contributed.append(routes)

    def register_binding(self, binding) -> None:
        self.bindings.append(binding)

    def remove_plugin(self, plugin_id: str) -> None:
        self.removed.append(plugin_id)


class _FakePlugin(Plugin):
    name = "fake_plugin"
    version = "1.0.0"
    instance_config = {"PROJECT_ID": "1"}


@pytest.mark.asyncio
async def test_plugin_on_load_routes_through_event_bus():
    """on_load enables tools and contributes routes via stub managers, not core.context."""
    stub_mcp = _StubMCP()
    stub_routes = _StubRouteRegistry()
    registry = PluginRegistry(
        stub_mcp,
        route_registry=stub_routes,
        http_route_registry=object(),
    )
    plugin = _FakePlugin()
    ctx = registry.lifecycle._get_or_create_context(plugin.name)
    sample_route = RouteDescriptor(
        plugin_id=plugin.name,
        operation_id=f"{plugin.name}__search",
        description="search",
    )

    with patch(
        "core.plugin_loader.plugin_context.PluginContext.disabled_tool_names",
        new_callable=AsyncMock,
        return_value={"hidden_tool"},
    ), patch(
        "core.proxy_tools.static_tool_loader.collect_from",
        return_value=[sample_route],
    ):
        await plugin.on_load(ctx)

    assert plugin.name in stub_mcp.enabled_tags
    assert stub_mcp.disabled_names == {"hidden_tool"}
    assert stub_routes.contributed == [[sample_route]]
    assert len(stub_routes.bindings) == 1
    assert stub_routes.bindings[0].plugin_id == plugin.name


@pytest.mark.asyncio
async def test_plugin_on_unload_routes_through_event_bus():
    """on_unload disables tools by tag and removes routes via the event bus."""
    stub_mcp = _StubMCP()
    stub_routes = _StubRouteRegistry()
    registry = PluginRegistry(
        stub_mcp,
        route_registry=stub_routes,
        http_route_registry=object(),
    )
    plugin = _FakePlugin()
    ctx = registry.lifecycle._get_or_create_context(plugin.name)

    await plugin.on_unload(ctx)

    assert plugin.name in stub_mcp.disabled_tags
    assert stub_routes.removed == [plugin.name]


@pytest.mark.asyncio
async def test_plugin_boot_clears_vault_via_emit_async():
    """plugin.boot event triggers auth vault clear without lifecycle→auth direct calls."""
    mock_vault = AsyncMock()
    registry = PluginRegistry(
        _StubMCP(),
        vault=mock_vault,
        route_registry=_StubRouteRegistry(),
        http_route_registry=object(),
    )

    await registry.events.emit_async("plugin.boot", "my_plugin")

    mock_vault.delete.assert_any_call("my_plugin", "direct_auth_token")
    mock_vault.delete.assert_any_call("my_plugin", "direct_auth_token_expires_at")


@pytest.mark.asyncio
async def test_initialize_plugins_uses_plugin_boot_event():
    """initialize_plugins clears direct tokens via plugin.boot, not a private auth call."""
    stub_mcp = _StubMCP()
    stub_routes = _StubRouteRegistry()
    mock_vault = AsyncMock()
    registry = PluginRegistry(
        stub_mcp,
        vault=mock_vault,
        route_registry=stub_routes,
        http_route_registry=object(),
    )
    _set_registry(registry)
    plugin = _FakePlugin()
    registry.lifecycle.register_plugin(plugin)

    with patch(
        "core.plugin_loader.plugin_context.PluginContext.disabled_tool_names",
        new_callable=AsyncMock,
        return_value=set(),
    ), patch(
        "core.proxy_tools.static_tool_loader.collect_from",
        return_value=[],
    ), patch.object(
        registry.auth,
        "get_auth_headers",
        new_callable=AsyncMock,
        return_value={},
    ), patch.object(
        registry.auth,
        "_vault_clear_direct_token",
        wraps=registry.auth._vault_clear_direct_token,
    ) as mock_clear:
        await registry.lifecycle.initialize_plugins()

    mock_clear.assert_called_once_with(plugin.name)
    mock_vault.delete.assert_any_call(plugin.name, "direct_auth_token")


class _JulesPlugin(Plugin):
    name = "jules_plugin"
    version = "1.0.0"


@pytest.mark.asyncio
async def test_reinitialize_lazy_loads_unregistered_plugin():
    """reinitialize_plugin lazy-loads plugins absent from _plugin_id_map at boot."""
    stub_mcp = _StubMCP()
    stub_routes = _StubRouteRegistry()
    registry = PluginRegistry(
        stub_mcp,
        route_registry=stub_routes,
        http_route_registry=object(),
    )
    _set_registry(registry)

    async def _fake_load(reg, plugin_id, config_path=None):
        registry.lifecycle.register_plugin(_JulesPlugin())
        return True

    with patch(
        "core.plugin_loader.plugin_loader.load_single_plugin",
        side_effect=_fake_load,
    ), patch.object(_JulesPlugin, "on_load", new_callable=AsyncMock) as mock_on_load, patch.object(
        _JulesPlugin, "on_ready", new_callable=AsyncMock
    ) as mock_on_ready, patch(
        "core_graph.worker.enqueue_pending",
        new_callable=AsyncMock,
        return_value=1,
    ) as mock_enqueue:
        await registry.lifecycle.reinitialize_plugin("jules_plugin")

    mock_on_load.assert_awaited_once()
    mock_on_ready.assert_awaited_once()
    mock_enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_reinitialize_proxy_delegates_to_proxy_manager():
    """reinitialize_plugin routes proxy_* ids to ProxyManager.enable_proxy."""
    registry = PluginRegistry(
        _StubMCP(),
        route_registry=_StubRouteRegistry(),
        http_route_registry=object(),
    )
    mock_enable = AsyncMock()

    with patch(
        "core.proxy.proxy_manager.proxy_manager.enable_proxy",
        mock_enable,
    ):
        await registry.lifecycle.reinitialize_plugin("proxy_notion")

    mock_enable.assert_awaited_once_with("notion")


@pytest.mark.asyncio
async def test_disable_proxy_removes_routes():
    """disable_proxy unmounts from FastMCP and drops route-registry contributions."""
    from core.proxy.proxy_manager import proxy_manager
    from core.route_registry.route_descriptor import RouteDescriptor
    from core.route_registry.route_registry import RouteRegistry

    route_reg = RouteRegistry()
    route_reg.contribute([
        RouteDescriptor(
            plugin_id="proxy_notion",
            operation_id="proxy_notion__fetch",
            description="fetch",
        ),
    ])
    assert len(route_reg.all_routes()) == 1

    with patch("core.proxy.proxy_manager.unmount_proxy") as mock_unmount, patch(
        "core.proxy.proxy_manager.get_async_session"
    ) as mock_session_ctx, patch(
        "core.proxy.proxy_manager.route_registry", route_reg, create=True,
    ), patch(
        "core.context.route_registry", route_reg,
    ):
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        await proxy_manager.disable_proxy("notion")

    mock_unmount.assert_called_once()
    assert len(route_reg.all_routes()) == 0


@pytest.mark.asyncio
async def test_teardown_proxy_delegates_to_proxy_manager():
    """teardown_plugin routes proxy_* ids to ProxyManager.disable_proxy."""
    registry = PluginRegistry(
        _StubMCP(),
        route_registry=_StubRouteRegistry(),
        http_route_registry=object(),
    )
    mock_disable = AsyncMock()

    with patch(
        "core.proxy.proxy_manager.proxy_manager.disable_proxy",
        mock_disable,
    ):
        await registry.lifecycle.teardown_plugin("proxy_notion")

    mock_disable.assert_awaited_once_with("notion")


@pytest.mark.asyncio
async def test_reinitialize_normalizes_dotted_plugin_id():
    """reinitialize_plugin normalizes dotted plugin ids for _plugin_id_map lookup."""
    stub_mcp = _StubMCP()
    registry = PluginRegistry(
        stub_mcp,
        route_registry=_StubRouteRegistry(),
        http_route_registry=object(),
    )
    plugin = _FakePlugin()
    registry.lifecycle.register_plugin(plugin)

    with patch.object(plugin, "on_load", new_callable=AsyncMock) as mock_on_load, patch.object(
        plugin, "on_ready", new_callable=AsyncMock
    ) as mock_on_ready, patch(
        "core.plugin_loader.plugin_loader.load_single_plugin",
        new_callable=AsyncMock,
    ) as mock_load:
        await registry.lifecycle.reinitialize_plugin("plugins.fake_plugin")

    mock_load.assert_not_awaited()
    mock_on_load.assert_awaited_once()
    mock_on_ready.assert_awaited_once()


@pytest.mark.asyncio
async def test_plugin_on_load_re_hides_hidden_tools():
    """on_load re-hides hidden tools via tool_visibility."""
    stub_mcp = _StubMCP()
    stub_routes = _StubRouteRegistry()
    registry = PluginRegistry(
        stub_mcp,
        route_registry=stub_routes,
        http_route_registry=object(),
    )
    plugin = _FakePlugin()
    ctx = registry.lifecycle._get_or_create_context(plugin.name)
    sample_route = RouteDescriptor(
        plugin_id=plugin.name,
        operation_id=f"{plugin.name}__search",
        description="search",
    )

    with patch(
        "core.plugin_loader.plugin_context.PluginContext.disabled_tool_names",
        new_callable=AsyncMock,
        return_value=set(),
    ), patch(
        "core.plugin_loader.plugin_context.PluginContext.hidden_tool_names",
        new_callable=AsyncMock,
        return_value={"hidden_tool_1"},
    ), patch(
        "core.proxy_tools.static_tool_loader.collect_from",
        return_value=[sample_route],
    ), patch(
        "core.context.tool_visibility.hide_gateway"
    ) as mock_hide:
        await plugin.on_load(ctx)

    assert plugin.name in stub_mcp.enabled_tags
    mock_hide.assert_called_once_with("hidden_tool_1")
    assert stub_routes.contributed == [[sample_route]]