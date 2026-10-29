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
    BackoffPolicy,
    BoundedSSETransport,
    BoundedStreamableHttpTransport,
    CoalescingProxyProvider,
    ProxyBackoffError,
    _install_coalescing,
    _proxy_backoff,
    _proxy_limits,
    build_proxy,
    set_exhausted_hook,
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


def _coalescing_provider(
    ttl_s: float = 60.0, backoff: BackoffPolicy | None = None
) -> CoalescingProxyProvider:
    """Build a ProxyProvider and upgrade it in place the way ``build_proxy`` does."""
    provider = ProxyProvider(lambda: None)
    assert _install_coalescing(SimpleNamespace(providers=[object(), provider]), ttl_s, backoff)
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
    """A failed fetch reaches every waiter and the next call (zero backoff) retries upstream."""
    provider = _coalescing_provider(backoff=BackoffPolicy(base_s=0.0, max_s=0.0))
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


def _always_failing(counter: dict):
    """Return a fake upstream list that counts calls and always raises."""
    async def fail(self):
        """Simulate an upstream answering 405."""
        counter["calls"] += 1
        raise RuntimeError("405 Method Not Allowed")
    return fail


@pytest.mark.asyncio
async def test_failure_backs_off_without_contacting_upstream():
    """Inside the backoff window calls fail fast with ProxyBackoffError and open no session."""
    provider = _coalescing_provider(backoff=BackoffPolicy(base_s=60.0, max_s=60.0, max_attempts=0))
    counter = {"calls": 0}
    with patch.object(ProxyProvider, "_list_prompts", _always_failing(counter)):
        with pytest.raises(RuntimeError, match="405"):
            await provider._list_prompts()
        for _ in range(20):
            with pytest.raises(ProxyBackoffError, match="backing off"):
                await provider._list_prompts()
    assert counter["calls"] == 1


@pytest.mark.asyncio
async def test_backoff_window_expires_and_kinds_are_independent():
    """After the window a retry reaches upstream; a failing kind never blocks another kind."""
    provider = _coalescing_provider(backoff=BackoffPolicy(base_s=60.0, max_s=60.0, max_attempts=0))
    counter = {"calls": 0}

    async def ok_tools(self):
        """Healthy tools listing."""
        return ["t"]

    with patch.object(ProxyProvider, "_list_prompts", _always_failing(counter)), \
         patch.object(ProxyProvider, "_list_tools", ok_tools):
        with pytest.raises(RuntimeError):
            await provider._list_prompts()
        assert await provider._list_tools() == ["t"]
        failures, _, err = provider._list_failures["prompts"]
        provider._list_failures["prompts"] = (failures, 0.0, err)  # window elapsed
        with pytest.raises(RuntimeError, match="405"):
            await provider._list_prompts()
    assert counter["calls"] == 2
    assert provider._list_failures["prompts"][0] == 2


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts_until_reset():
    """The attempt cap stops reconnecting; ``reset_backoff`` re-enables it, success clears state."""
    provider = _coalescing_provider(backoff=BackoffPolicy(base_s=0.0, max_s=0.0, max_attempts=3))
    counter = {"calls": 0}
    with patch.object(ProxyProvider, "_list_resources", _always_failing(counter)):
        for _ in range(3):
            with pytest.raises(RuntimeError, match="405"):
                await provider._list_resources()
        for _ in range(5):
            with pytest.raises(ProxyBackoffError, match="gave up after 3"):
                await provider._list_resources()
    assert counter["calls"] == 3

    async def recovered(self):
        """Upstream is healthy again."""
        return ["r"]

    provider.reset_backoff("resources")
    with patch.object(ProxyProvider, "_list_resources", recovered):
        assert await provider._list_resources() == ["r"]
    assert "resources" not in provider._list_failures


@pytest.mark.asyncio
async def test_exhausted_hook_fires_once_with_kind_and_count():
    """Giving up calls the hook exactly once; later fail-fast calls never re-fire it."""
    provider = _coalescing_provider(backoff=BackoffPolicy(base_s=0.0, max_s=0.0, max_attempts=2))
    fired = []
    handle = SimpleNamespace(server=SimpleNamespace(providers=[provider]))
    assert set_exhausted_hook(handle, lambda *a: fired.append(a))
    counter = {"calls": 0}
    with patch.object(ProxyProvider, "_list_tools", _always_failing(counter)):
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await provider._list_tools()
        for _ in range(3):
            with pytest.raises(ProxyBackoffError):
                await provider._list_tools()
    assert fired == [("tools", 2, "405 Method Not Allowed")]


def test_set_exhausted_hook_ignores_handles_without_coalescing():
    """A test double / foreign handle is a silent no-op, not an AttributeError."""
    assert set_exhausted_hook(object(), lambda *a: None) is False


def test_backoff_delay_is_exponential_capped_and_jittered():
    """Delay doubles per failure, never exceeds the cap, and stays within ``[d/2, d]``."""
    policy = BackoffPolicy(base_s=1.0, max_s=10.0, max_attempts=5)
    for failures, full in [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 10.0), (9, 10.0)]:
        for _ in range(20):
            assert full / 2 <= policy.delay(failures) <= full
    assert not policy.exhausted(4) and policy.exhausted(5)
    assert not BackoffPolicy(max_attempts=0).exhausted(10_000)
    # Retry-forever mode must not overflow int->float after ~1024 failures.
    assert 150.0 <= BackoffPolicy(max_s=300.0, max_attempts=0).delay(5_000) <= 300.0


def test_proxy_backoff_reads_config_and_clamps(caplog):
    """``graph.proxy_reconnect_*`` keys configure the policy; negatives clamp with a warning."""
    registry = SimpleNamespace(server={"graph": {
        "proxy_reconnect_base_s": 2, "proxy_reconnect_max_s": 30, "proxy_reconnect_max_attempts": 7,
    }})
    with patch("utils.config_registry.get_config_registry", return_value=registry):
        policy = _proxy_backoff()
    assert (policy.base_s, policy.max_s, policy.max_attempts) == (2.0, 30.0, 7)

    registry = SimpleNamespace(server={"graph": {"proxy_reconnect_max_attempts": -1}})
    with caplog.at_level("WARNING", logger="whiskers.plugins"), \
         patch("utils.config_registry.get_config_registry", return_value=registry):
        policy = _proxy_backoff()
    assert (policy.base_s, policy.max_s, policy.max_attempts) == (2.0, 300.0, 0)
    assert "clamping" in caplog.text


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


# --- bounded queue, acquire timeout, global cap, circuit breaker -------------------------


@contextlib.asynccontextmanager
async def _held_session(self, **kwargs):
    """Upstream session double that stays open until the caller exits."""
    yield object()


@pytest.mark.asyncio
async def test_full_queue_rejects_immediately_with_proxy_busy():
    """Cap 1 + queue 1: the third concurrent session fails fast instead of queueing."""
    transport = BoundedStreamableHttpTransport("http://upstream.test/mcp", max_sessions=1)
    transport._session_gate.max_waiters = 1
    release = asyncio.Event()

    async def hold():
        """Open a session and keep it until released."""
        async with transport.connect_session():
            await release.wait()

    with patch.object(StreamableHttpTransport, "connect_session", _held_session):
        holder = asyncio.ensure_future(hold())
        waiter = asyncio.ensure_future(hold())
        await asyncio.sleep(0.01)
        assert (transport._session_gate.open, transport._session_gate.waiting) == (1, 1)
        with pytest.raises(compose.ProxyBusyError, match="queue full") as info:
            async with transport.connect_session():
                pass
        assert info.value.timed_out is False
        release.set()
        await asyncio.gather(holder, waiter)
    assert (transport._session_gate.open, transport._session_gate.waiting) == (0, 0)
    assert transport._breaker.failures == 0  # load shedding is not an upstream fault


@pytest.mark.asyncio
async def test_acquire_timeout_raises_and_restores_counts():
    """A waiter past the acquire timeout raises ProxyBusyError and leaves no slot or waiter behind."""
    transport = BoundedStreamableHttpTransport("http://upstream.test/mcp", max_sessions=1)
    transport._session_gate.timeout_s = 0.02
    release = asyncio.Event()

    async def hold():
        """Hold the only slot."""
        async with transport.connect_session():
            await release.wait()

    with patch.object(StreamableHttpTransport, "connect_session", _held_session):
        holder = asyncio.ensure_future(hold())
        await asyncio.sleep(0.01)
        with pytest.raises(compose.ProxyBusyError, match="no slot") as info:
            async with transport.connect_session():
                pass
        assert info.value.timed_out is True
        assert transport._session_gate.waiting == 0
        release.set()
        await holder
        async with transport.connect_session():  # the slot is reusable
            assert transport._session_gate.open == 1
    assert transport._session_gate.open == 0
    assert transport._session_gate._sem._value == 1  # no over-release


@pytest.mark.asyncio
async def test_cancelled_waiter_releases_nothing():
    """Cancelling a queued waiter neither leaks a waiter count nor over-releases the semaphore."""
    transport = BoundedStreamableHttpTransport("http://upstream.test/mcp", max_sessions=1)
    release = asyncio.Event()

    async def hold():
        """Hold the only slot."""
        async with transport.connect_session():
            await release.wait()

    with patch.object(StreamableHttpTransport, "connect_session", _held_session):
        holder = asyncio.ensure_future(hold())
        await asyncio.sleep(0.01)
        waiter = asyncio.ensure_future(hold())
        await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await holder
    gate = transport._session_gate
    assert (gate.open, gate.waiting, gate._sem._value) == (0, 0, 1)


@pytest.mark.asyncio
async def test_global_gate_caps_sessions_across_proxies():
    """Two proxies with cap 4 each still share one global cap of 3."""
    registry = SimpleNamespace(server={"graph": {"proxy_max_total_sessions": 3}})
    live = 0
    peak = 0

    @contextlib.asynccontextmanager
    async def fake_connect(self, **kwargs):
        """Track open upstream sessions across both transports."""
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            await asyncio.sleep(0.01)
            yield object()
        finally:
            live -= 1

    with patch.object(compose, "_global_gate", None), \
         patch("utils.config_registry.get_config_registry", return_value=registry), \
         patch.object(StreamableHttpTransport, "connect_session", fake_connect):
        a = BoundedStreamableHttpTransport("http://a.test/mcp", max_sessions=4)
        b = BoundedStreamableHttpTransport("http://b.test/mcp", max_sessions=4)

        async def one(t):
            """Open and release one session."""
            async with t.connect_session():
                await asyncio.sleep(0.01)

        await asyncio.gather(*(one(t) for t in (a, b) for _ in range(6)))
    assert peak == 3


def _failing_connect(counter: dict):
    """Upstream session double whose connect always raises."""
    @contextlib.asynccontextmanager
    async def fail(self, **kwargs):
        """Simulate an upstream refusing the session."""
        counter["calls"] += 1
        raise RuntimeError("401 Unauthorized")
        yield  # pragma: no cover
    return fail


@pytest.mark.asyncio
async def test_breaker_opens_and_fails_fast_without_upstream_calls():
    """After a failed session, calls inside the window raise ProxyBackoffError without connecting."""
    transport = BoundedStreamableHttpTransport(
        "http://upstream.test/mcp", backoff=BackoffPolicy(base_s=60.0, max_s=60.0, max_attempts=0)
    )
    counter = {"calls": 0}
    with patch.object(StreamableHttpTransport, "connect_session", _failing_connect(counter)):
        with pytest.raises(RuntimeError, match="401"):
            async with transport.connect_session():
                pass
        for _ in range(10):
            with pytest.raises(ProxyBackoffError, match="backing off"):
                async with transport.connect_session():
                    pass
    assert counter["calls"] == 1
    assert transport._breaker.is_open()
    assert compose.session_stats()["breakers_open"] >= 1


@pytest.mark.asyncio
async def test_breaker_counts_one_concurrent_burst_once():
    """Sessions admitted together that fail together count as one failure."""
    transport = BoundedStreamableHttpTransport(
        "http://upstream.test/mcp", backoff=BackoffPolicy(base_s=0.0, max_s=0.0, max_attempts=3)
    )

    @contextlib.asynccontextmanager
    async def slow_fail(self, **kwargs):
        """Fail after a beat, so the whole burst is admitted first."""
        await asyncio.sleep(0.01)
        raise RuntimeError("502")
        yield  # pragma: no cover

    async def one():
        """Open one session."""
        async with transport.connect_session():
            pass

    with patch.object(StreamableHttpTransport, "connect_session", slow_fail):
        results = await asyncio.gather(*(one() for _ in range(8)), return_exceptions=True)
    assert all(isinstance(r, RuntimeError) for r in results)
    assert transport._breaker.failures == 1


@pytest.mark.asyncio
async def test_breaker_gives_up_fires_session_hook_once_and_success_resets():
    """The attempt cap fires the exhausted hook with kind ``session`` exactly once."""
    handle = build_proxy("breaker", "http", "http://127.0.0.1:9/mcp")
    inner = next(p for p in handle.server.providers if isinstance(p, CoalescingProxyProvider))
    transport = inner.client_factory().transport
    breaker = transport._breaker
    breaker.backoff = BackoffPolicy(base_s=0.0, max_s=0.0, max_attempts=2)
    fired = []
    assert set_exhausted_hook(handle, lambda *a: fired.append(a))
    counter = {"calls": 0}
    with patch.object(StreamableHttpTransport, "connect_session", _failing_connect(counter)):
        for _ in range(2):
            with pytest.raises(RuntimeError):
                async with transport.connect_session():
                    pass
        for _ in range(3):
            with pytest.raises(ProxyBackoffError, match="gave up after 2"):
                async with transport.connect_session():
                    pass
    assert counter["calls"] == 2
    assert fired == [("session", 2, "401 Unauthorized")]

    breaker.success()
    with patch.object(StreamableHttpTransport, "connect_session", _held_session):
        async with transport.connect_session():
            pass
    assert breaker.failures == 0 and not breaker.is_open()


@pytest.mark.asyncio
async def test_clean_session_after_failure_closes_breaker():
    """One healthy session resets the consecutive-failure count."""
    transport = BoundedStreamableHttpTransport(
        "http://upstream.test/mcp", backoff=BackoffPolicy(base_s=0.0, max_s=0.0, max_attempts=5)
    )
    counter = {"calls": 0}
    with patch.object(StreamableHttpTransport, "connect_session", _failing_connect(counter)):
        with pytest.raises(RuntimeError):
            async with transport.connect_session():
                pass
    assert transport._breaker.failures == 1
    with patch.object(StreamableHttpTransport, "connect_session", _held_session):
        async with transport.connect_session():
            pass
    assert transport._breaker.failures == 0


# --- SSE GET 405 memo ---------------------------------------------------------------------


def _counting_upstream(status: int, calls: list):
    """httpx2 mock transport answering every request with ``status`` and recording it."""
    import httpx2

    def handler(request):
        """Record the request and answer with the fixed status."""
        calls.append((request.method, str(request.url)))
        return httpx2.Response(status)

    return httpx2.MockTransport(handler)


@pytest.mark.asyncio
async def test_sse_get_405_is_memoized_and_posts_still_go_upstream():
    """The first SSE GET 405 goes upstream; later ones are answered locally; POSTs are untouched."""
    calls: list = []
    url = "https://stateless.test/mcp"
    with patch.object(compose, "_get_405_memo", {}):
        client = compose._get_get405_client_cls()(transport=_counting_upstream(405, calls))
        sse = {"Accept": "text/event-stream"}
        async with client:
            for _ in range(5):
                assert (await client.get(url, headers=sse)).status_code == 405
            await client.post(url, headers={"Accept": "application/json, text/event-stream"})
            await client.get(url)  # a plain GET is not the stream probe
    assert calls == [("GET", url), ("POST", url), ("GET", url)]


@pytest.mark.asyncio
async def test_sse_get_405_memo_expires_and_can_be_disabled():
    """An expired memo re-probes upstream; ``proxy_get_405_memo_s: 0`` never memoizes."""
    calls: list = []
    url = "https://stateless.test/mcp"
    sse = {"Accept": "text/event-stream"}
    with patch.object(compose, "_get_405_memo", {}) as memo:
        client = compose._get_get405_client_cls()(transport=_counting_upstream(405, calls))
        async with client:
            await client.get(url, headers=sse)
            memo[url] = 0.0  # expired
            await client.get(url, headers=sse)
        assert len(calls) == 2

    registry = SimpleNamespace(server={"graph": {"proxy_get_405_memo_s": 0}})
    calls.clear()
    with patch.object(compose, "_get_405_memo", {}), \
         patch("utils.config_registry.get_config_registry", return_value=registry):
        client = compose._get_get405_client_cls()(transport=_counting_upstream(405, calls))
        async with client:
            for _ in range(3):
                await client.get(url, headers=sse)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_shared_factory_builds_the_memo_client():
    """Upstream transports get the memoizing client from the shared factory."""
    with patch.object(compose, "_shared_ssl_ctx", None):
        client = compose._shared_http_client_factory()
    async with client:
        assert isinstance(client, compose._get_get405_client_cls())
