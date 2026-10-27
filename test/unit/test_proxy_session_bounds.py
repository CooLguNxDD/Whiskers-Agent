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
    build_proxy,
)


@pytest.mark.asyncio
async def test_session_gate_caps_concurrent_upstream_sessions():
    transport = BoundedStreamableHttpTransport("http://upstream.test/mcp", max_sessions=2)
    live = 0
    peak = 0

    @contextlib.asynccontextmanager
    async def fake_connect(self, **kwargs):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            await asyncio.sleep(0.01)
            yield object()
        finally:
            live -= 1

    async def one_session():
        async with transport.connect_session():
            await asyncio.sleep(0.01)

    with patch.object(StreamableHttpTransport, "connect_session", fake_connect):
        await asyncio.gather(*(one_session() for _ in range(10)))

    assert peak == 2
    assert live == 0


def test_bounded_transports_use_shared_client_factory():
    http = BoundedStreamableHttpTransport("http://upstream.test/mcp")
    sse = BoundedSSETransport("http://upstream.test/sse")
    assert http.httpx_client_factory is compose._shared_http_client_factory
    assert sse.httpx_client_factory is compose._shared_http_client_factory


@pytest.mark.asyncio
async def test_shared_client_factory_builds_tls_context_once():
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
    provider = ProxyProvider(lambda: None)
    assert _install_coalescing(SimpleNamespace(providers=[object(), provider]), ttl_s)
    assert isinstance(provider, CoalescingProxyProvider)
    return provider


@pytest.mark.asyncio
async def test_concurrent_lists_share_one_upstream_fetch_and_memoize():
    provider = _coalescing_provider()
    calls = 0

    async def fake_list(self):
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
    provider = _coalescing_provider(ttl_s=0.0)
    calls = {"tools": 0, "prompts": 0}

    async def fake_tools(self):
        calls["tools"] += 1
        return []

    async def fake_prompts(self):
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
    provider = _coalescing_provider()
    calls = 0

    async def flaky(self):
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
    provider = _coalescing_provider()
    release = asyncio.Event()

    async def slow(self):
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
