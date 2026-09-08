"""Unit tests for gateway (run_graph unified) tool-hiding visibility."""

import pytest

from core.proxy_tools.tool_visibility import ToolVisibility, GATEWAY_ALWAYS_VISIBLE


class _FakeMcp:
    """Records disable/enable transform calls without a real FastMCP registry."""

    def __init__(self):
        self.disabled: set[str] = set()

    def disable(self, names=None, components=None):
        self.disabled |= set(names or [])

    def enable(self, names=None, components=None):
        self.disabled -= set(names or [])


def test_apply_hidden_hides_names_and_skips_allowlist():
    """apply_hidden disables given tools but never the always-visible allowlist."""
    mcp = _FakeMcp()
    tv = ToolVisibility(mcp)
    count = tv.apply_hidden(["create_record", "send_message", "run_graph", "discover_tools"])
    assert count == 2
    assert mcp.disabled == {"create_record", "send_message"}
    assert "run_graph" not in mcp.disabled
    assert "discover_tools" not in mcp.disabled


def test_apply_hidden_empty_is_noop():
    """No names → no transform, returns 0."""
    mcp = _FakeMcp()
    assert ToolVisibility(mcp).apply_hidden([]) == 0
    assert mcp.disabled == set()


def test_apply_hidden_unbound_returns_zero():
    """Unbound visibility manager hides nothing."""
    assert ToolVisibility(None).apply_hidden(["create_record"]) == 0


def test_hide_gateway_skips_allowlisted_tool():
    """hide_gateway refuses to hide an allowlisted entry point."""
    mcp = _FakeMcp()
    tv = ToolVisibility(mcp)
    assert tv.hide_gateway("run_graph") is False
    assert mcp.disabled == set()


def test_hide_and_show_gateway_roundtrip():
    """hide_gateway then show_gateway restores exposure."""
    mcp = _FakeMcp()
    tv = ToolVisibility(mcp)
    assert tv.hide_gateway("list_records") is True
    assert "list_records" in mcp.disabled
    assert tv.show_gateway("list_records") is True
    assert "list_records" not in mcp.disabled


def test_allowlist_contains_core_entries():
    """Gateway entry point and auth tools are in the always-visible allowlist."""
    assert {
        "run_graph",
        "discover_tools",
        "authenticate",
        "complete_authentication",
        "fetch_artifact",
    } <= GATEWAY_ALWAYS_VISIBLE
    # list/get are registered but not always-visible (deep-read = fetch only).
    assert "list_artifacts" not in GATEWAY_ALWAYS_VISIBLE
    assert "get_artifact" not in GATEWAY_ALWAYS_VISIBLE
    # PortfolioAgent_run must NOT be allowlisted: it's a plugin-level write
    # entrypoint into the full portfolio agent. Skipping the scope check here
    # would let any authenticated caller (incl. a narrowly-scoped public ask
    # key) bypass ask mode and reach it with zero scopes.
    assert "PortfolioAgent_run" not in GATEWAY_ALWAYS_VISIBLE


def test_proxy_exposed_name():
    """proxy_exposed_name resolves namespaced names for proxies and keeps others unchanged."""
    from core.proxy_tools.tool_visibility import proxy_exposed_name
    assert proxy_exposed_name("proxy_github", "search_repos") == "github_search_repos"
    assert proxy_exposed_name("fake_plugin", "run_graph") == "run_graph"


def test_apply_persisted_with_proxy():
    """apply_persisted maps proxy tool names before disabling them."""
    mcp = _FakeMcp()
    tv = ToolVisibility(mcp)
    
    disabled_keys = [
        ("proxy_github", "search_repos"),
        ("fake_plugin", "create_record"),
    ]
    
    count = tv.apply_persisted(disabled_keys)
    assert count == 2
    assert mcp.disabled == {"github_search_repos", "create_record"}


async def test_reapply_hidden_for_plugin():
    """reapply_hidden_for_plugin applies disabled tools and gateway/selective hides for the plugin."""
    from unittest.mock import AsyncMock, patch
    mcp = _FakeMcp()

    with patch("db_layer.tool_config_store.get_disabled_tools", new_callable=AsyncMock) as mock_disabled, \
         patch(
             "db_layer.gateway_settings_store.collect_gateway_hidden_names_for_plugin",
             new_callable=AsyncMock,
         ) as mock_collect:

        mock_disabled.return_value = {
            ("proxy_github", "search_repos"),
            ("proxy_gitlab", "list_issues"),
        }
        # Collector already branches unified vs selective internally.
        mock_collect.return_value = {"github_other_tool"}

        from core.context import tool_visibility
        from core.proxy_tools.tool_visibility import reapply_hidden_for_plugin
        original_mcp = tool_visibility._mcp
        tool_visibility._mcp = mcp

        try:
            await reapply_hidden_for_plugin("proxy_github")

            assert "github_search_repos" in mcp.disabled
            assert "gitlab_list_issues" not in mcp.disabled
            assert "github_other_tool" in mcp.disabled
            mock_collect.assert_called_once_with("proxy_github")
        finally:
            tool_visibility._mcp = original_mcp


async def test_reapply_hidden_for_plugin_always_calls_collector():
    """Even when gateway is OFF, selective re-hide still runs via the collector."""
    from unittest.mock import AsyncMock, patch
    mcp = _FakeMcp()

    with patch("db_layer.tool_config_store.get_disabled_tools", new_callable=AsyncMock) as mock_disabled, \
         patch(
             "db_layer.gateway_settings_store.collect_gateway_hidden_names_for_plugin",
             new_callable=AsyncMock,
         ) as mock_collect:
        mock_disabled.return_value = set()
        mock_collect.return_value = {"selectively_hidden"}

        from core.context import tool_visibility
        from core.proxy_tools.tool_visibility import reapply_hidden_for_plugin
        original_mcp = tool_visibility._mcp
        tool_visibility._mcp = mcp
        try:
            await reapply_hidden_for_plugin("fake_plugin")
            assert "selectively_hidden" in mcp.disabled
        finally:
            tool_visibility._mcp = original_mcp


async def test_hide_proxy_tools_if_gateway_maps_namespace():
    """Proxy tools are hidden as namespace_rawName when gateway unified is ON."""
    from unittest.mock import AsyncMock, MagicMock, patch
    mcp = _FakeMcp()

    tools = [MagicMock(name="t1"), MagicMock(name="t2")]
    tools[0].name = "ListSessions"
    tools[1].name = "GetSession"

    with patch(
        "db_layer.gateway_settings_store.get_gateway_unified",
        new_callable=AsyncMock,
        return_value=True,
    ):
        from core.context import tool_visibility
        from core.proxy_tools.tool_visibility import hide_proxy_tools_if_gateway
        original_mcp = tool_visibility._mcp
        tool_visibility._mcp = mcp
        try:
            count = await hide_proxy_tools_if_gateway("jules", tools)
            assert count == 2
            assert "jules_ListSessions" in mcp.disabled
            assert "jules_GetSession" in mcp.disabled
        finally:
            tool_visibility._mcp = original_mcp


async def test_hide_proxy_tools_if_gateway_off_is_noop():
    """When gateway is OFF, proxy hide helper does nothing."""
    from unittest.mock import AsyncMock, MagicMock, patch
    mcp = _FakeMcp()
    t = MagicMock()
    t.name = "ListSessions"
    with patch(
        "db_layer.gateway_settings_store.get_gateway_unified",
        new_callable=AsyncMock,
        return_value=False,
    ):
        from core.context import tool_visibility
        from core.proxy_tools.tool_visibility import hide_proxy_tools_if_gateway
        original_mcp = tool_visibility._mcp
        tool_visibility._mcp = mcp
        try:
            assert await hide_proxy_tools_if_gateway("jules", [t]) == 0
            assert mcp.disabled == set()
        finally:
            tool_visibility._mcp = original_mcp

