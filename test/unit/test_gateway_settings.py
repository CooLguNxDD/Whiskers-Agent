"""
Unit tests for gateway_settings_store (Part A + unified hide collectors).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_get_gateway_unified_falls_back_to_json_default_when_no_db():
    from db_layer import gateway_settings_store as gs
    with patch.object(gs, "_db_available", return_value=False):
        val = await gs.get_gateway_unified()
        from utils.server_config import GATEWAY_UNIFIED as default
        assert val == default


@pytest.mark.asyncio
async def test_set_and_get_roundtrip_when_db_available():
    from db_layer import gateway_settings_store as gs
    with patch.object(gs, "_db_available", return_value=True):
        # mock session
        fake_row = None
        async def fake_exec(*a, **k):
            class R: pass
            r = R()
            r.fetchone = lambda: fake_row
            return r
        with patch("db_layer.gateway_settings_store.get_async_session") as mock_sess:
            cm = AsyncMock()
            cm.__aenter__.return_value.execute = AsyncMock(side_effect=fake_exec)
            cm.__aenter__.return_value.commit = AsyncMock()
            mock_sess.return_value = cm

            # set
            res = await gs.set_gateway_unified(True)
            assert res is True

            # simulate row present
            class Row:
                def __init__(self):
                    self._v = {"enabled": True}
                def __getitem__(self, i): return self._v if i == 0 else None
            with patch("db_layer.gateway_settings_store.get_async_session") as mock_sess2:
                cm2 = AsyncMock()
                cm2.__aenter__.return_value.execute = AsyncMock(return_value=type("R", (), {"fetchone": lambda s: Row()})())
                mock_sess2.return_value = cm2
                val = await gs.get_gateway_unified()
                assert val is True


@pytest.mark.asyncio
async def test_collect_all_mcp_tool_names_includes_proxy_namespace():
    """Provider walk includes namespaced proxy tools (namespace_toolname)."""
    from db_layer import gateway_settings_store as gs

    local = MagicMock()
    local.name = "get_record"
    proxy = MagicMock()
    proxy.name = "jules_ListSessions"

    async def _list_all(mcp_app):
        return {"run_graph", "get_record", "jules_ListSessions", "authenticate"}

    with patch("core.context.mcp", object()), \
         patch("core.proxy_tools.fastmcp_adapter.list_all_tool_names", side_effect=_list_all), \
         patch.object(gs, "_collect_proxy_route_exposed_names", new=AsyncMock(return_value=set())):
        names = await gs.collect_all_mcp_tool_names()
    assert "jules_ListSessions" in names
    assert "get_record" in names


@pytest.mark.asyncio
async def test_collect_proxy_route_fallback_gathers_plugin_names():
    """When private routes map is unavailable, fan-out collect_plugin_exposed_tool_names via gather."""
    from db_layer import gateway_settings_store as gs

    class _RR:
        _routes = None  # force fallback path

    class _Reg:
        route_registry = _RR()

    async def _fake_collect(pid):
        return {f"{pid}__tool"}

    class _Result:
        def all(self):
            return [("proxy_a",), ("proxy_b",)]

    class _Sess:
        async def execute(self, *a, **k):
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with patch("core.plugin_loader.plugin_registry.get_registry", return_value=_Reg()), \
         patch("db_layer.gateway_settings_store.get_async_session", return_value=_Sess()), \
         patch.object(gs, "collect_plugin_exposed_tool_names", side_effect=_fake_collect) as mock_collect:
        names = await gs._collect_proxy_route_exposed_names()
    assert names == {"proxy_a__tool", "proxy_b__tool"}
    assert mock_collect.await_count == 2


@pytest.mark.asyncio
async def test_collect_gateway_unified_hide_names_excludes_allowlist():
    """Unified hide set is every live MCP tool minus GATEWAY_ALWAYS_VISIBLE."""
    from db_layer import gateway_settings_store as gs
    from core.proxy_tools.tool_visibility import GATEWAY_ALWAYS_VISIBLE

    with patch.object(
        gs,
        "collect_all_mcp_tool_names",
        new=AsyncMock(return_value={
            "run_graph", "discover_tools", "get_record", "list_sessions",
            "authenticate", "complete_authentication", "create_record",
            "jules_ListSessions", "github_search_repos",
        }),
    ):
        unified = await gs.collect_gateway_unified_hide_names()
    assert unified == {
        "get_record", "list_sessions", "create_record",
        "jules_ListSessions", "github_search_repos",
    }
    assert unified.isdisjoint(GATEWAY_ALWAYS_VISIBLE)

    with patch.object(
        gs,
        "collect_gateway_unified_hide_names",
        new=AsyncMock(return_value={"foo", "bar"}),
    ):
        assert await gs.collect_gateway_hidden_names() == {"foo", "bar"}


@pytest.mark.asyncio
async def test_collect_gateway_hidden_names_for_plugin_unified_vs_selective():
    """Plugin reapply uses full plugin tool set when unified ON, selective when OFF."""
    from db_layer import gateway_settings_store as gs

    with patch.object(gs, "get_gateway_unified", new=AsyncMock(return_value=True)), \
         patch.object(
             gs, "collect_plugin_exposed_tool_names",
             new=AsyncMock(return_value={"a", "b"}),
         ), \
         patch.object(
             gs, "collect_selective_hidden_names_for_plugin",
             new=AsyncMock(return_value={"only_flagged"}),
         ):
        assert await gs.collect_gateway_hidden_names_for_plugin("p1") == {"a", "b"}

    with patch.object(gs, "get_gateway_unified", new=AsyncMock(return_value=False)), \
         patch.object(
             gs, "collect_plugin_exposed_tool_names",
             new=AsyncMock(return_value={"a", "b"}),
         ), \
         patch.object(
             gs, "collect_selective_hidden_names_for_plugin",
             new=AsyncMock(return_value={"only_flagged"}),
         ):
        assert await gs.collect_gateway_hidden_names_for_plugin("p1") == {"only_flagged"}
