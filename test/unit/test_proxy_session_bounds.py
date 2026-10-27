"""Upstream proxy memory bounds: session cap, shared TLS context, coalesced list fetches.

Regression for the 4 GB OOM where agent fan-out against the Notion/GitHub proxies
opened one fresh upstream session (own httpx2 client + TLS context) per call with no
cap, and every ``ProxyProvider`` cache miss re-downloaded the upstream catalog.
"""

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.server.providers.proxy import ProxyProvider

import core.proxy.compose as compose
from core.proxy.compose import (
    BoundedSSETransport,
    BoundedStreamableHttpTransport,
    CoalescingProxyProvider,
    _install_coalescing,
    _proxy_limits,
    build_proxy,
)


@pytest.mark.asyncio
async def test_session_gate_caps_concurrent_upstream_sessions():
    """Ten concurrent sessions never exceed the per-proxy cap of two."""
    transport = BoundedStreamableHttpTransport("http://upstream.test/mcp", max_sessions=2)
    live = 0
    peak = 0

    @contextlib.asynccontextmanager
    async def fake_connect(self, **kwargs):
        """Track how many upstream sessions are open at once."""
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            await asyncio.sleep(0.01)
            yield object()
        finally:
            live -= 1

    async def one_session():
        """Open and briefly hold one upstream session."""
        async with transport.connect_session():
            await asyncio.sleep(0.01)

    with patch.object(StreamableHttpTransport, "connect_session", fake_connect):
        await asyncio.gather(*(one_session() for _ in range(10)))

    assert peak == 2
    assert live == 0


def test_bounded_transports_use_shared_client_factory():
    """Both bounded transports build httpx2 clients from the shared-TLS factory."""
    http = BoundedStreamableHttpTransport("http://upstream.test/mcp")
    sse = BoundedSSETransport("http://upstream.test/sse")
    assert http.httpx_client_factory is compose._shared_http_client_factory
    assert sse.httpx_client_factory is compose._shared_http_client_factory


@pytest.mark.asyncio
async def test_shared_client_factory_builds_tls_context_once():
    """The TLS context is created once and reused; MCP default timeouts apply."""
    import httpx2

    real = httpx2.create_ssl_context
    with patch.object(compose, "_shared_ssl_ctx", None), \
         patch("httpx2.create_ssl_context", side_effect=real) as spy:
        a = compose._shared_http_client_factory(headers={"x": "1"})
        b = compose._shared_http_client_factory(follow_redirects=True, timeout=None)
        async with a, b:
            assert a.headers["x"] == "1"
            assert a.timeout.read == 300.0
        assert spy.call_count == 1


def _coalescing_provider(ttl_s: float = 60.0) -> CoalescingProxyProvider:
    """Build a ProxyProvider and upgrade it in place the way ``build_proxy`` does."""
    provider = ProxyProvider(lambda: None)
    assert _install_coalescing(SimpleNamespace(providers=[object(), provider]), ttl_s)
    assert isinstance(provider, CoalescingProxyProvider)
    return provider


@pytest.mark.asyncio
async def test_concurrent_lists_share_one_upstream_fetch_and_memoize():
    """25 concurrent lists hit upstream once; a follow-up within TTL is a memo hit."""
    provider = _coalescing_provider()
    calls = 0

    async def fake_list(self):
        """Count upstream list calls."""
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return ["t1", "t2"]

    with patch.object(ProxyProvider, "_list_tools", fake_list):
        results = await asyncio.gather(*(provider._list_tools() for _ in range(25)))
        assert calls == 1
        assert all(r == ["t1", "t2"] for r in results)
        await provider._list_tools()  # within TTL -> memo hit
        assert calls == 1


@pytest.mark.asyncio
async def test_list_memo_expires_and_kinds_are_independent():
    """A zero TTL refetches every time, and each list kind has its own memo."""
    provider = _coalescing_provider(ttl_s=0.0)
    calls = {"tools": 0, "prompts": 0}

    async def fake_tools(self):
        """Count upstream tools lists."""
        calls["tools"] += 1
        return []

    async def fake_prompts(self):
        """Count upstream prompts lists."""
        calls["prompts"] += 1
        return []

    with patch.object(ProxyProvider, "_list_tools", fake_tools), \
         patch.object(ProxyProvider, "_list_prompts", fake_prompts):
        await provider._list_tools()
        await provider._list_tools()
        await provider._list_prompts()
    assert calls == {"tools": 2, "prompts": 1}


@pytest.mark.asyncio
async def test_list_errors_propagate_and_are_not_memoized():
    """A failed fetch reaches every waiter and the next call retries upstream."""
    provider = _coalescing_provider()
    calls = 0

    async def flaky(self):
        """Fail on the first upstream call, succeed afterwards."""
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        if calls == 1:
            raise RuntimeError("upstream down")
        return ["ok"]

    with patch.object(ProxyProvider, "_list_tools", flaky):
        results = await asyncio.gather(
            *(provider._list_tools() for _ in range(5)), return_exceptions=True
        )
        assert all(isinstance(r, RuntimeError) for r in results)
        assert await provider._list_tools() == ["ok"]
    assert calls == 2


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_fetch():
    """Cancelling one waiter leaves the shared fetch running for the others."""
    provider = _coalescing_provider()
    release = asyncio.Event()

    async def slow(self):
        """Block until released."""
        await release.wait()
        return ["done"]

    with patch.object(ProxyProvider, "_list_tools", slow):
        first = asyncio.ensure_future(provider._list_tools())
        second = asyncio.ensure_future(provider._list_tools())
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(0)
        release.set()
        assert await second == ["done"]
        assert first.cancelled()


def test_build_proxy_wires_bounded_transport_and_coalescing_provider():
    """Guards FastMCP upgrades: the in-place provider swap must still find ProxyProvider."""
    handle = build_proxy("bounds", "http", "http://127.0.0.1:9/mcp")
    inner = [p for p in handle.server.providers if isinstance(p, ProxyProvider)]
    assert len(inner) == 1
    assert isinstance(inner[0], CoalescingProxyProvider)
    client = inner[0].client_factory()
    assert isinstance(client.transport, BoundedStreamableHttpTransport)


def _limits_with(graph: dict) -> tuple[int, float]:
    """Resolve ``_proxy_limits`` against a stubbed ``graph`` config block."""
    registry = SimpleNamespace(server={"graph": graph})
    with patch("utils.config_registry.get_config_registry", return_value=registry):
        return _proxy_limits()


def test_proxy_limits_read_config_and_default():
    """Configured values are used; missing keys fall back to the defaults."""
    assert _limits_with({"proxy_max_concurrent_sessions": 3, "proxy_list_cache_ttl_s": 5}) == (3, 5.0)
    assert _limits_with({}) == (8, 60.0)


def test_proxy_limits_invalid_value_warns_without_dropping_the_other(caplog):
    """One bad key warns and defaults alone; the valid sibling key still applies."""
    with caplog.at_level("WARNING", logger="whiskers.plugins"):
        limits = _limits_with({"proxy_max_concurrent_sessions": "lots", "proxy_list_cache_ttl_s": 5})
    assert limits == (8, 5.0)
    assert "proxy_max_concurrent_sessions" in caplog.text


def test_proxy_limits_clamp_below_minimum(caplog):
    """A zero session cap would deadlock every call, so it clamps to 1 with a warning."""
    with caplog.at_level("WARNING", logger="whiskers.plugins"):
        assert _limits_with({"proxy_max_concurrent_sessions": 0, "proxy_list_cache_ttl_s": -1}) == (1, 0.0)
    assert "clamping" in caplog.text
