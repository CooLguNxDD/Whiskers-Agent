"""Unit tests for core.context.tool_meta — local-only tool metadata resolution.

Guards against the regression this module fixes: calling FastMCP.get_tool()/
list_tools() on a composite server fans out to every mounted proxy provider
(and, for disabled/hidden tools, forces a full upstream re-list). These tests
prove resolve_tool_meta() never touches a provider's get_tool/list_tools.
"""

from __future__ import annotations

import pytest

from core.context import tool_meta
from core.context.tool_meta import ToolMeta, resolve_tool_meta


class FakeTool:
    """Mock for a FastMCP Tool object as returned by fastmcp_adapter.iter_tools()."""

    def __init__(self, name, tags=(), parameters=None):
        self.name = name
        self.tags = tags
        self.parameters = parameters or {}


class FakeLocalProvider:
    def __init__(self, tools):
        self._components = {f"tool:{t.name}": t for t in tools}


class _ExplodingProvider:
    """A provider whose get_tool/list_tools raise if ever called.

    Used to prove resolve_tool_meta() never contacts a mounted provider for
    metadata — only reads the static ``transforms`` namespace prefix.
    """

    def __init__(self, prefix: str):
        class _NS:
            _prefix = prefix

        self.transforms = [_NS()]

    async def get_tool(self, *a, **k):
        raise AssertionError("resolve_tool_meta must not call provider.get_tool()")

    async def list_tools(self, *a, **k):
        raise AssertionError("resolve_tool_meta must not call provider.list_tools()")


class FakeContext:
    """Minimal MiddlewareContext stand-in exposing fastmcp_context.fastmcp."""

    def __init__(self, app):
        class _FastMCPCtx:
            fastmcp = app

        self.fastmcp_context = _FastMCPCtx()


@pytest.fixture(autouse=True)
def _reset_index():
    """Force a fresh local-index build each test (avoid cross-test cache bleed)."""
    tool_meta.bump_version()
    yield
    tool_meta.bump_version()


@pytest.fixture
def mock_registry(monkeypatch):
    class _Registry:
        def get(self, name):
            return None

    monkeypatch.setattr("core.context._registries.route_registry", _Registry())


@pytest.mark.asyncio
async def test_local_tool_resolves_from_static_index(mock_registry):
    """A locally-registered @mcp.tool (e.g. semantic_search_records) resolves
    without any provider being consulted, and picks up declares_shape from its
    own input schema."""
    tool = FakeTool(
        name="semantic_search_records",
        tags=("fake_plugin",),
        parameters={"properties": {"query": {"type": "string"}}},
    )
    app = type("App", (), {})()
    app._local_provider = FakeLocalProvider([tool])
    app.providers = [_ExplodingProvider("github-andrew")]

    meta = await resolve_tool_meta(FakeContext(app), "semantic_search_records")

    assert isinstance(meta, ToolMeta)
    assert meta.tags == ("fake_plugin",)
    assert meta.declares_shape is False


@pytest.mark.asyncio
async def test_local_tool_declares_response_shape(mock_registry):
    """A generated safe_api_call tool with _response_shape in its schema is flagged."""
    tool = FakeTool(
        name="ListSessions",
        tags=("jules_plugin",),
        parameters={"properties": {"_response_shape": {"type": "object"}}},
    )
    app = type("App", (), {})()
    app._local_provider = FakeLocalProvider([tool])
    app.providers = []

    meta = await resolve_tool_meta(FakeContext(app), "ListSessions")

    assert meta.declares_shape is True


@pytest.mark.asyncio
async def test_proxy_prefixed_name_resolves_without_contacting_provider(mock_registry):
    """A namespaced proxy tool name resolves plugin_id from the mount's namespace
    prefix alone — the exploding provider proves get_tool/list_tools are never called."""
    app = type("App", (), {})()
    app._local_provider = FakeLocalProvider([])
    app.providers = [_ExplodingProvider("github-andrew")]

    meta = await resolve_tool_meta(FakeContext(app), "github-andrew_list_pull_requests")

    assert meta.plugin_id == "proxy_github-andrew"


@pytest.mark.asyncio
async def test_unknown_tool_resolves_to_empty_without_contacting_provider(mock_registry):
    """A miss across route_registry, local index, and proxy prefixes fails open to
    empty metadata — still without touching any provider."""
    app = type("App", (), {})()
    app._local_provider = FakeLocalProvider([])
    app.providers = [_ExplodingProvider("notion")]

    meta = await resolve_tool_meta(FakeContext(app), "totally_unknown_tool")

    assert meta.plugin_id == ""
    assert meta.tags == ()
    assert meta.declares_shape is False


@pytest.mark.asyncio
async def test_per_request_memo_dedupes_within_one_call(mock_registry, monkeypatch):
    """Two lookups of the same name within one request context resolve once."""
    calls = {"n": 0}

    class _CountingRegistry:
        def get(self, name):
            calls["n"] += 1
            return None

    monkeypatch.setattr("core.context._registries.route_registry", _CountingRegistry())

    tool = FakeTool(name="semantic_search_records", tags=("fake_plugin",))
    app = type("App", (), {})()
    app._local_provider = FakeLocalProvider([tool])
    app.providers = []
    ctx = FakeContext(app)

    first = await resolve_tool_meta(ctx, "semantic_search_records")
    second = await resolve_tool_meta(ctx, "semantic_search_records")

    assert first == second
    assert calls["n"] == 1
