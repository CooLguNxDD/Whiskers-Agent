"""Unit tests for core.context.scope_middleware.ScopeEnforcementMiddleware."""

import os
from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError

from core.context.scope_middleware import ScopeEnforcementMiddleware


class FakeMessage:
    """Mock for message containing the tool name."""

    def __init__(self, name: str):
        self.name = name


class FakeContext:
    """Mock for MiddlewareContext containing the message."""

    def __init__(self, name: str):
        self.message = FakeMessage(name)


class FakeToken:
    """Mock for AccessToken containing scopes list."""

    def __init__(self, scopes: list[str] | None):
        self.scopes = scopes


class FakeCallNext:
    """Mock call_next callback tracking invocation."""

    def __init__(self):
        self.called = False

    async def __call__(self, context):
        self.called = True
        return "allowed"


@pytest.fixture(autouse=True)
def _enforcement_on(monkeypatch):
    """Force scope enforcement on regardless of server_config.json."""
    monkeypatch.setattr(
        "core.scope_management.policy.scope_enforcement_enabled", lambda: True
    )
    monkeypatch.setattr(
        "core.scope_management.policy.get_enforcement_mode", lambda: "enforce"
    )


@pytest.fixture
def middleware():
    """Fixture to instantiate the middleware."""
    return ScopeEnforcementMiddleware()


@pytest.fixture
def mock_registry(monkeypatch):
    """Fixture to mock route_registry in core.context._registries."""
    registry = MagicMock()
    monkeypatch.setattr("core.context._registries.route_registry", registry)
    return registry


@pytest.mark.asyncio
async def test_case_1_deny_all_plugin_tool(middleware, mock_registry, monkeypatch):
    """1. deny-all (token.scopes == []) calling a plugin tool whose route resolves to a
    non-empty required set -> raises fastmcp.exceptions.ToolError (call_next NOT invoked).
    """
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "false")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: FakeToken(scopes=[]),
    )

    desc = MagicMock()
    desc.plugin_id = "jules_plugin"
    desc.tags = ("jules_tag",)
    mock_registry.get.return_value = desc

    context = FakeContext(name="julescreate_session")
    call_next = FakeCallNext()

    with pytest.raises(ToolError) as exc_info:
        await middleware.on_call_tool(context, call_next)

    assert "Scope denied" in str(exc_info.value)
    assert not call_next.called


@pytest.mark.asyncio
async def test_case_2_matching_plugin_scope(middleware, mock_registry, monkeypatch):
    """2. token.scopes == ["plugin:jules_plugin"] calling that jules tool -> call_next invoked (allowed)."""
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "false")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: FakeToken(scopes=["plugin:jules_plugin"]),
    )

    desc = MagicMock()
    desc.plugin_id = "jules_plugin"
    desc.tags = ("jules_tag",)
    mock_registry.get.return_value = desc

    context = FakeContext(name="julescreate_session")
    call_next = FakeCallNext()

    res = await middleware.on_call_tool(context, call_next)

    assert res == "allowed"
    assert call_next.called


@pytest.mark.asyncio
async def test_case_3_matching_group_scope(middleware, mock_registry, monkeypatch):
    """3. token.scopes containing the matching "group:<plugin>:<tag>" -> allowed."""
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "false")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: FakeToken(scopes=["group:jules_plugin:jules_tag"]),
    )

    desc = MagicMock()
    desc.plugin_id = "jules_plugin"
    desc.tags = ("jules_tag",)
    mock_registry.get.return_value = desc

    context = FakeContext(name="julescreate_session")
    call_next = FakeCallNext()

    res = await middleware.on_call_tool(context, call_next)

    assert res == "allowed"
    assert call_next.called


@pytest.mark.asyncio
async def test_case_admin_scope_bypass(middleware, mock_registry, monkeypatch):
    """`admin` master scope bypasses per-plugin gating even when the required
    plugin token is absent -> allowed."""
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "false")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: FakeToken(scopes=["whiskers", "admin"]),
    )

    desc = MagicMock()
    desc.plugin_id = "jules_plugin"
    desc.tags = ("jules_tag",)
    mock_registry.get.return_value = desc

    context = FakeContext(name="julescreate_session")
    call_next = FakeCallNext()

    res = await middleware.on_call_tool(context, call_next)

    assert res == "allowed"
    assert call_next.called


@pytest.mark.asyncio
async def test_case_4_gateway_always_visible(middleware, mock_registry, monkeypatch):
    """4. tool name in GATEWAY_ALWAYS_VISIBLE (e.g. "run_graph") with deny-all token -> allowed."""
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "false")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: FakeToken(scopes=[]),
    )

    context = FakeContext(name="run_graph")
    call_next = FakeCallNext()

    res = await middleware.on_call_tool(context, call_next)

    assert res == "allowed"
    assert call_next.called
    assert not mock_registry.get.called


@pytest.mark.asyncio
async def test_case_5_token_none(middleware, mock_registry, monkeypatch):
    """5. get_access_token() returns None (unauthenticated/local) -> allowed."""
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "false")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: None,
    )

    context = FakeContext(name="julescreate_session")
    call_next = FakeCallNext()

    res = await middleware.on_call_tool(context, call_next)

    assert res == "allowed"
    assert call_next.called


@pytest.mark.asyncio
async def test_case_6_unknown_tool(middleware, mock_registry, monkeypatch):
    """6. Unknown tool (empty required) with authenticated empty scopes → deny (C09)."""
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "false")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: FakeToken(scopes=[]),
    )

    mock_registry.get.return_value = None

    context = FakeContext(name="unknown_tool")
    call_next = FakeCallNext()

    with pytest.raises(ToolError) as exc_info:
        await middleware.on_call_tool(context, call_next)

    assert "Scope denied" in str(exc_info.value) or "scope" in str(exc_info.value).lower()
    assert not call_next.called


@pytest.mark.asyncio
async def test_case_7_dangerously_skip_permissions(middleware, mock_registry, monkeypatch):
    """7. env DANGEROUSLY_SKIP_PERMISSIONS == "true" -> allowed regardless of scopes."""
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "true")
    monkeypatch.setenv("WHISKERS_TRANSPORT", "stdio")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: FakeToken(scopes=[]),
    )

    desc = MagicMock()
    desc.plugin_id = "jules_plugin"
    desc.tags = ("jules_tag",)
    mock_registry.get.return_value = desc

    context = FakeContext(name="julescreate_session")
    call_next = FakeCallNext()

    res = await middleware.on_call_tool(context, call_next)

    assert res == "allowed"
    assert call_next.called


@pytest.mark.asyncio
async def test_case_7_dangerously_skip_permissions_with_http_still_gated(middleware, mock_registry, monkeypatch):
    """env DANGEROUSLY_SKIP_PERMISSIONS == "true" + WHISKERS_TRANSPORT == "http" -> still gated."""
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "true")
    monkeypatch.setenv("WHISKERS_TRANSPORT", "http")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: FakeToken(scopes=[]),
    )

    desc = MagicMock()
    desc.plugin_id = "jules_plugin"
    desc.tags = ("jules_tag",)
    mock_registry.get.return_value = desc

    context = FakeContext(name="julescreate_session")
    call_next = FakeCallNext()

    with pytest.raises(ToolError) as exc_info:
        await middleware.on_call_tool(context, call_next)

    assert "scope denied" in str(exc_info.value).lower()
    assert not call_next.called


class FakeTool:
    """Mock for a FastMCP Tool object exposing .name/.tags (as returned by iter_tools)."""

    def __init__(self, name, tags):
        self.name = name
        self.tags = tags


class FakeLocalProvider:
    """Mock for FastMCP's ``_local_provider`` — backs core.proxy_tools.fastmcp_adapter.iter_tools()."""

    def __init__(self, tool):
        self._components = {f"tool:{tool.name}": tool}


class FakeFastMCPApp:
    """Mock for the FastMCP app exposing the local-provider component index.

    tool_meta.resolve_tool_meta resolves dynamic-tools-registered (non
    route_registry-indexed) tools via the local static tool index
    (``fastmcp_adapter.iter_tools``), never via ``app.get_tool()`` — that call
    fans out to every mounted proxy provider on a real composite server.
    """

    def __init__(self, tool):
        self._tool = tool
        self._local_provider = FakeLocalProvider(tool)
        self.providers = []


class FakeFastMCPContext:
    """Mock for Context exposing .fastmcp."""

    def __init__(self, app):
        self.fastmcp = app


class FakeContextWithFastMCP(FakeContext):
    """FakeContext variant carrying a fastmcp_context for the dynamic-tools fallback path."""

    def __init__(self, name: str, app):
        super().__init__(name)
        self.fastmcp_context = FakeFastMCPContext(app)


@pytest.mark.asyncio
async def test_dynamic_tool_deny_all_denied(middleware, mock_registry, monkeypatch):
    """Dynamic-tools-registered tool (route_registry miss, e.g. jules_plugin) with a
    deny-all key must still be denied via the FastMCP tool-tags fallback path."""
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "false")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: FakeToken(scopes=[]),
    )
    mock_registry.get.return_value = None

    fake_registry = MagicMock()
    fake_registry.lifecycle._plugin_id_map = {"jules_plugin": object()}
    monkeypatch.setattr(
        "core.plugin_loader.plugin_registry.get_registry",
        lambda: fake_registry,
    )

    app = FakeFastMCPApp(FakeTool(name="juleslist_sessions", tags={"activity", "write_update", "jules_plugin"}))
    context = FakeContextWithFastMCP(name="juleslist_sessions", app=app)
    call_next = FakeCallNext()

    with pytest.raises(ToolError) as exc_info:
        await middleware.on_call_tool(context, call_next)

    assert "Scope denied" in str(exc_info.value)
    assert not call_next.called


@pytest.mark.asyncio
async def test_dynamic_tool_plugin_wide_scope_allowed(middleware, mock_registry, monkeypatch):
    """Dynamic-tools-registered tool with a matching plugin:<id> scope is allowed."""
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "false")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: FakeToken(scopes=["plugin:jules_plugin"]),
    )
    mock_registry.get.return_value = None

    fake_registry = MagicMock()
    fake_registry.lifecycle._plugin_id_map = {"jules_plugin": object()}
    monkeypatch.setattr(
        "core.plugin_loader.plugin_registry.get_registry",
        lambda: fake_registry,
    )

    app = FakeFastMCPApp(FakeTool(name="juleslist_sessions", tags={"activity", "write_update", "jules_plugin"}))
    context = FakeContextWithFastMCP(name="juleslist_sessions", app=app)
    call_next = FakeCallNext()

    res = await middleware.on_call_tool(context, call_next)

    assert res == "allowed"
    assert call_next.called


@pytest.mark.asyncio
async def test_registry_lookup_exception(middleware, mock_registry, monkeypatch):
    """Registry lookup error → empty required → authenticated deny (fail-closed)."""
    monkeypatch.setenv("DANGEROUSLY_SKIP_PERMISSIONS", "false")
    monkeypatch.setattr(
        "core.context.scope_middleware.get_access_token",
        lambda: FakeToken(scopes=[]),
    )

    mock_registry.get.side_effect = Exception("registry error")

    context = FakeContext(name="some_tool")
    call_next = FakeCallNext()

    with pytest.raises(ToolError):
        await middleware.on_call_tool(context, call_next)

    assert not call_next.called
