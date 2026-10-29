"""FastMCP composition logic for Whiskers Agent upstream proxies.

Memory bounding (FastMCP opens a *fresh* upstream session per proxied request —
initialize + GET stream + call + DELETE, each with its own ``httpx2.AsyncClient``):

- every session's client reuses one shared TLS context instead of re-loading the
  CA bundle (~1.4 MB + CPU per client);
- a per-proxy gate plus one global gate cap concurrently open upstream sessions;
  the queue behind each gate is bounded too (``proxy_max_queued_sessions``) and a
  waiter gives up after ``proxy_session_acquire_timeout_s`` with ``ProxyBusyError``,
  so agent fan-out is shed instead of piling up unbounded waiters (the cap alone
  only moved the OOM from open sessions into the queue);
- a per-proxy circuit breaker counts upstream session failures (connect,
  initialize, transport errors, acquire timeouts) on *every* path, tool calls
  included; while open it rejects without queueing, and once it gives up it fires
  the same exhausted hook as the list backoff so ``ProxyManager`` unmounts the proxy;
- ``tools``/``prompts``/``resources`` listings are coalesced (concurrent misses
  share one upstream fetch) and served from a short TTL memo, so neither a burst
  of front ``tools/list`` calls nor ``ProxyProvider``'s 300s lookup-cache expiry
  re-downloads the upstream catalog once per caller;
- a failed listing backs off exponentially per list kind (fail fast inside the
  window, no upstream session opened) and stops reconnecting after
  ``proxy_reconnect_max_attempts`` consecutive failures, so an upstream that keeps
  answering 405 cannot be hammered by every front ``*/list`` call.
"""

import asyncio
import contextlib
import logging
import random
import ssl
import time
import weakref
from typing import Any, Awaitable, Callable

from fastmcp import FastMCP
from fastmcp.server import create_proxy
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider
from fastmcp.server.providers.fastmcp_provider import FastMCPProvider

logger = logging.getLogger("whiskers.plugins")

_DEFAULT_MAX_CONCURRENT_SESSIONS = 8
_DEFAULT_LIST_CACHE_TTL_S = 60.0
_DEFAULT_RECONNECT_BASE_S = 2.0
_DEFAULT_RECONNECT_MAX_S = 300.0
_DEFAULT_RECONNECT_MAX_ATTEMPTS = 6
_DEFAULT_MAX_QUEUED_SESSIONS = 16
_DEFAULT_SESSION_ACQUIRE_TIMEOUT_S = 15.0
_DEFAULT_MAX_TOTAL_SESSIONS = 16
_BUSY_LOG_INTERVAL_S = 60.0


def _config_number(graph: dict, key: str, default: Any, cast: Callable[[Any], Any], floor: Any) -> Any:
    """Read one numeric ``graph`` key; warn and fall back to ``default`` on an invalid value."""
    raw = graph.get(key, default)
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid graph.%s=%r; using default %r", key, raw, default)
        return default
    if value < floor:
        logger.warning("graph.%s=%r below minimum %r; clamping", key, raw, floor)
        return floor
    return value


def _graph_config() -> dict:
    """Return the server ``graph`` config block, or ``{}`` when the registry is unavailable."""
    try:
        from utils.config_registry import get_config_registry
        return get_config_registry().server.get("graph", {}) or {}
    except Exception:
        logger.debug("proxy limits config unavailable; using defaults", exc_info=True)
        return {}


def _proxy_limits() -> tuple[int, float]:
    """Return ``(max_concurrent_sessions, list_cache_ttl_s)`` from ``graph`` config, with defaults."""
    graph = _graph_config()
    sessions = _config_number(graph, "proxy_max_concurrent_sessions", _DEFAULT_MAX_CONCURRENT_SESSIONS, int, 1)
    ttl = _config_number(graph, "proxy_list_cache_ttl_s", _DEFAULT_LIST_CACHE_TTL_S, float, 0.0)
    return sessions, ttl


class BackoffPolicy:
    """Exponential reconnect backoff: ``base * 2**(n-1)`` capped at ``max_s``, full jitter on top half.

    ``max_attempts`` consecutive failures stop reconnecting entirely; ``0`` means retry forever.
    """

    __slots__ = ("base_s", "max_s", "max_attempts")

    def __init__(
        self,
        base_s: float = _DEFAULT_RECONNECT_BASE_S,
        max_s: float = _DEFAULT_RECONNECT_MAX_S,
        max_attempts: int = _DEFAULT_RECONNECT_MAX_ATTEMPTS,
    ):
        self.base_s = base_s
        self.max_s = max(max_s, base_s)
        self.max_attempts = max_attempts

    def delay(self, failures: int) -> float:
        """Seconds to wait after the ``failures``-th consecutive failure (jittered in ``[d/2, d]``)."""
        # Clamp the exponent: retry-forever mode would otherwise overflow int->float near 2**1024.
        exp = min(max(failures - 1, 0), 62)
        d = min(self.base_s * (2 ** exp), self.max_s)
        return d / 2 + random.random() * d / 2

    def exhausted(self, failures: int) -> bool:
        """True once ``failures`` reached the attempt cap (never, when the cap is 0)."""
        return self.max_attempts > 0 and failures >= self.max_attempts


def _proxy_backoff() -> BackoffPolicy:
    """Build the reconnect ``BackoffPolicy`` from ``graph.proxy_reconnect_*`` config, with defaults."""
    graph = _graph_config()
    return BackoffPolicy(
        base_s=_config_number(graph, "proxy_reconnect_base_s", _DEFAULT_RECONNECT_BASE_S, float, 0.0),
        max_s=_config_number(graph, "proxy_reconnect_max_s", _DEFAULT_RECONNECT_MAX_S, float, 0.0),
        max_attempts=_config_number(
            graph, "proxy_reconnect_max_attempts", _DEFAULT_RECONNECT_MAX_ATTEMPTS, int, 0
        ),
    )


class ProxyBackoffError(RuntimeError):
    """Raised without contacting upstream while a list kind is backing off or has given up."""


# (kind, consecutive_failures, last_error) -> None; fired once when a kind gives up.
ExhaustedHook = Callable[[str, int, str], None]


_shared_ssl_ctx: ssl.SSLContext | None = None

_DEFAULT_GET_405_MEMO_S = 3600.0
# url -> monotonic expiry: upstreams that answered the SSE GET stream with 405 (stateless servers
# such as Notion / GitHub Copilot MCP). The SDK treats 405 as "no stream" and still re-probes on
# every session; the memo answers those probes locally instead of hitting upstream again.
_get_405_memo: dict[str, float] = {}


def _is_sse_get(request: Any) -> bool:
    """True for the SDK's standalone server-to-client stream probe (GET + text/event-stream)."""
    return request.method == "GET" and "text/event-stream" in request.headers.get("accept", "")


def _remember_get_405(url: str) -> None:
    """Memoize a 405 on ``url``'s SSE GET for ``graph.proxy_get_405_memo_s`` (0 disables)."""
    ttl = _config_number(_graph_config(), "proxy_get_405_memo_s", _DEFAULT_GET_405_MEMO_S, float, 0.0)
    if ttl <= 0:
        return
    now = time.monotonic()
    for stale in [u for u, exp in _get_405_memo.items() if exp <= now]:
        del _get_405_memo[stale]
    if url not in _get_405_memo:
        logger.info("Proxy upstream %s does not serve the SSE GET stream (405); skipping it for %.0fs", url, ttl)
    _get_405_memo[url] = now + ttl


def _get_405_memoized(url: str) -> bool:
    """True while ``url``'s SSE GET is known to 405."""
    exp = _get_405_memo.get(url)
    if exp is None:
        return False
    if exp <= time.monotonic():
        _get_405_memo.pop(url, None)
        return False
    return True


_get405_client_cls: type | None = None


def _get_get405_client_cls() -> type:
    """Build (once) the ``httpx2.AsyncClient`` subclass that short-circuits memoized SSE GET 405s.

    Overrides ``send`` rather than passing a custom ``transport=``: env-proxy mounts
    (``HTTPS_PROXY``) take precedence over ``transport`` and would bypass a wrapper.
    """
    global _get405_client_cls
    if _get405_client_cls is not None:
        return _get405_client_cls
    import httpx2

    class _Get405MemoClient(httpx2.AsyncClient):
        """AsyncClient that answers a known-405 SSE GET locally and records new ones."""

        async def send(self, request, **kwargs):  # type: ignore[override]
            """Send ``request``; a memoized SSE GET 405 never reaches the network."""
            if not _is_sse_get(request):
                return await super().send(request, **kwargs)
            url = str(request.url)
            if _get_405_memoized(url):
                return httpx2.Response(405, request=request)
            response = await super().send(request, **kwargs)
            if response.status_code == 405:
                _remember_get_405(url)
            return response

    _get405_client_cls = _Get405MemoClient
    return _get405_client_cls


def _shared_http_client_factory(
    headers: dict[str, str] | None = None,
    timeout: Any = None,
    auth: Any = None,
    follow_redirects: bool = True,
    **_ignored: Any,
) -> Any:
    """``httpx_client_factory`` for upstream transports: MCP default timeouts, one shared TLS context."""
    global _shared_ssl_ctx
    import httpx2

    if _shared_ssl_ctx is None:
        # Same trust store httpx2 would build per client (honors SSL_CERT_FILE/DIR).
        _shared_ssl_ctx = httpx2.create_ssl_context()
    if timeout is None:
        timeout = httpx2.Timeout(30.0, read=300.0)
    return _get_get405_client_cls()(
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=follow_redirects,
        verify=_shared_ssl_ctx,
    )


class ProxyBusyError(RuntimeError):
    """Raised when an upstream session slot is unavailable: queue full or acquire timed out."""

    def __init__(self, message: str, *, timed_out: bool = False, gate: str = ""):
        super().__init__(message)
        self.timed_out = timed_out
        self.gate = gate  # label of the SessionGate that rejected


def _gate_limits() -> tuple[int, float]:
    """Return ``(max_queued_sessions, session_acquire_timeout_s)`` from ``graph`` config."""
    graph = _graph_config()
    queued = _config_number(graph, "proxy_max_queued_sessions", _DEFAULT_MAX_QUEUED_SESSIONS, int, 0)
    timeout = _config_number(
        graph, "proxy_session_acquire_timeout_s", _DEFAULT_SESSION_ACQUIRE_TIMEOUT_S, float, 0.1
    )
    return queued, timeout


class SessionGate:
    """Semaphore with a bounded wait queue and an acquire timeout; tracks open/waiting counts."""

    def __init__(self, label: str, limit: int, max_waiters: int, timeout_s: float):
        self.label = label
        self.limit = limit
        self.max_waiters = max_waiters
        self.timeout_s = timeout_s
        self.open = 0
        self.waiting = 0
        self._sem = asyncio.Semaphore(limit)
        self._last_busy_log = 0.0

    def _busy(self, reason: str, *, timed_out: bool = False) -> ProxyBusyError:
        """Build the rejection, logging at most once per ``_BUSY_LOG_INTERVAL_S``."""
        now = time.monotonic()
        if now - self._last_busy_log >= _BUSY_LOG_INTERVAL_S:
            self._last_busy_log = now
            logger.warning(
                "Proxy session gate %s busy (%s): open=%d/%d waiting=%d/%d",
                self.label, reason, self.open, self.limit, self.waiting, self.max_waiters,
            )
        return ProxyBusyError(
            f"upstream session gate {self.label} busy: {reason}", timed_out=timed_out, gate=self.label
        )

    @contextlib.asynccontextmanager
    async def slot(self):
        """Hold one slot for the block; reject instead of queueing past the caps."""
        if self._sem.locked() and self.waiting >= self.max_waiters:
            raise self._busy("queue full")
        self.waiting += 1
        try:
            async with asyncio.timeout(self.timeout_s):
                await self._sem.acquire()
        except TimeoutError:
            raise self._busy(f"no slot within {self.timeout_s:g}s", timed_out=True) from None
        finally:
            self.waiting -= 1
        self.open += 1
        try:
            yield
        finally:
            self.open -= 1
            self._sem.release()


# One gate across every proxy, so N proxies cannot hold N * per-proxy-cap sessions.
# Rebuilt per event loop (a Semaphore binds to the loop it first waits on).
_global_gate: SessionGate | None = None
_global_gate_loop: asyncio.AbstractEventLoop | None = None


def _get_global_gate() -> SessionGate:
    """Return the process-wide upstream session gate for the running loop."""
    global _global_gate, _global_gate_loop
    loop = asyncio.get_running_loop()
    if _global_gate is None or _global_gate_loop is not loop:
        total = _config_number(
            _graph_config(), "proxy_max_total_sessions", _DEFAULT_MAX_TOTAL_SESSIONS, int, 1
        )
        queued, timeout = _gate_limits()
        # Every proxy's queue drains into this gate, so give it room for several proxies.
        _global_gate = SessionGate("global", total, max(queued * 4, total), timeout)
        _global_gate_loop = loop
    return _global_gate


# Live transports, for the ``proxy_sessions_*`` telemetry gauges.
_live_transports: "weakref.WeakSet[_SessionGateMixin]" = weakref.WeakSet()


def session_stats() -> dict[str, int]:
    """Aggregate open/waiting upstream sessions and open breakers across live proxy transports."""
    gates = [t._session_gate for t in list(_live_transports)]
    return {
        "open": sum(g.open for g in gates),
        "waiting": sum(g.waiting for g in gates),
        "breakers_open": sum(1 for t in list(_live_transports) if t._breaker.is_open()),
    }


class CircuitBreaker:
    """Consecutive upstream session failures -> fail fast inside a backoff window, then give up.

    Failures are counted per *generation*: sessions admitted together that then fail
    together (one upstream outage hitting a burst) count once, so a single bad burst
    cannot exhaust the attempt cap on its own.
    """

    def __init__(self, label: str, backoff: BackoffPolicy | None = None):
        self.label = label
        self.backoff = backoff or BackoffPolicy()
        self.failures = 0
        self.retry_at = 0.0
        self.last_error = ""
        self.generation = 0
        self.on_exhausted: ExhaustedHook | None = None

    def is_open(self) -> bool:
        """True while calls are rejected (inside the window, or given up)."""
        if self.failures == 0:
            return False
        return self.backoff.exhausted(self.failures) or time.monotonic() < self.retry_at

    def check(self) -> int:
        """Return the admitting generation; raise ``ProxyBackoffError`` while the breaker is open."""
        if self.failures == 0:
            return self.generation
        if self.backoff.exhausted(self.failures):
            raise ProxyBackoffError(
                f"upstream {self.label} gave up after {self.failures} failed sessions: {self.last_error}"
            )
        remaining = self.retry_at - time.monotonic()
        if remaining > 0:
            raise ProxyBackoffError(
                f"upstream {self.label} backing off {remaining:.1f}s "
                f"after {self.failures} failed session(s): {self.last_error}"
            )
        return self.generation

    def success(self) -> None:
        """Close the breaker."""
        if self.failures:
            self.generation += 1
        self.failures = 0
        self.retry_at = 0.0

    def failure(self, exc: BaseException, generation: int) -> None:
        """Count a failed session admitted in ``generation``; fire ``on_exhausted`` once at the cap."""
        if generation != self.generation or self.backoff.exhausted(self.failures):
            return  # same burst already counted, or already gave up: never re-fire the hook
        self.generation += 1
        self.failures += 1
        delay = self.backoff.delay(self.failures)
        self.retry_at = time.monotonic() + delay
        self.last_error = str(exc)[:500] or type(exc).__name__
        if self.backoff.exhausted(self.failures):
            logger.error(
                "Proxy upstream %s: %d consecutive failed sessions; giving up until reconnect: %s",
                self.label, self.failures, self.last_error,
            )
            if self.on_exhausted is not None:
                try:
                    self.on_exhausted("session", self.failures, self.last_error)
                except Exception:
                    logger.exception("Proxy exhausted hook failed for %s sessions", self.label)
        else:
            logger.warning(
                "Proxy upstream %s session failed (attempt %d); next retry in %.1fs: %s",
                self.label, self.failures, delay, self.last_error,
            )


class _SessionGateMixin:
    """Caps, bounds the queue for, and circuit-breaks upstream sessions per proxy.

    ``Client.new()`` shares the transport, so every proxied request passes through here.
    """

    _session_gate: SessionGate
    _breaker: CircuitBreaker

    def _init_gate(self, url: str, max_sessions: int, backoff: BackoffPolicy | None) -> None:
        """Build the per-proxy gate and breaker (called from the transport ``__init__``)."""
        queued, timeout = _gate_limits()
        self._session_gate = SessionGate(url, max_sessions, queued, timeout)
        self._breaker = CircuitBreaker(url, backoff)
        _live_transports.add(self)

    @contextlib.asynccontextmanager
    async def connect_session(self, **kwargs: Any):
        """Hold a per-proxy and a global slot for the whole upstream session lifetime."""
        generation = self._breaker.check()  # open breaker: fail fast, no queue, no upstream session
        try:
            async with self._session_gate.slot(), _get_global_gate().slot():
                # FastMCP runs this context in its session-runner task: initialize and
                # transport errors raise through it; a caller's tool error does not.
                async with super().connect_session(**kwargs) as session:  # type: ignore[misc]
                    yield session
        except ProxyBusyError as exc:
            # Only this proxy's own gate timing out says anything about this upstream
            # (its slots never free = hung sessions); a full global gate is other proxies' load.
            if exc.timed_out and exc.gate == self._session_gate.label:
                self._breaker.failure(exc, generation)
            raise  # a full queue is load shedding, not an upstream fault
        except Exception as exc:
            self._breaker.failure(exc, generation)
            raise
        else:
            self._breaker.success()


class BoundedStreamableHttpTransport(_SessionGateMixin, StreamableHttpTransport):
    """``StreamableHttpTransport`` with a bounded, circuit-broken session gate and shared TLS context."""

    def __init__(
        self,
        url: str,
        auth: Any = None,
        *,
        max_sessions: int = _DEFAULT_MAX_CONCURRENT_SESSIONS,
        backoff: BackoffPolicy | None = None,
    ):
        super().__init__(url=url, auth=auth, httpx_client_factory=_shared_http_client_factory)
        self._init_gate(url, max_sessions, backoff)


class BoundedSSETransport(_SessionGateMixin, SSETransport):
    """``SSETransport`` with a bounded, circuit-broken session gate and shared TLS context."""

    def __init__(
        self,
        url: str,
        auth: Any = None,
        *,
        max_sessions: int = _DEFAULT_MAX_CONCURRENT_SESSIONS,
        backoff: BackoffPolicy | None = None,
    ):
        super().__init__(url=url, auth=auth, httpx_client_factory=_shared_http_client_factory)
        self._init_gate(url, max_sessions, backoff)


class CoalescingProxyProvider(ProxyProvider):
    """``ProxyProvider`` whose list fetches are single-flight, briefly memoized, and backed off.

    Errors are never memoized as results; a waiter's cancellation never cancels the shared
    fetch. Consecutive failures of one list kind schedule its next upstream attempt with
    exponential backoff and stop reconnecting after the policy's attempt cap; one success
    (or ``reset_backoff``) clears it.
    """

    _list_ttl_s: float
    _list_memo: dict[str, tuple[float, Any]]
    _list_inflight: dict[str, asyncio.Future]
    _backoff: BackoffPolicy
    # kind -> (consecutive_failures, retry_at_monotonic, last_error_text)
    _list_failures: dict[str, tuple[int, float, str]]
    _on_exhausted: ExhaustedHook | None
    _session_breaker: "CircuitBreaker | None"

    def _init_coalescing(self, ttl_s: float, backoff: BackoffPolicy | None = None) -> None:
        """Initialize memo + backoff state (called after the in-place class swap in ``build_proxy``)."""
        self._list_ttl_s = ttl_s
        self._list_memo = {}
        self._list_inflight = {}
        self._backoff = backoff or BackoffPolicy()
        self._list_failures = {}
        self._on_exhausted = None
        self._session_breaker = None

    def reset_backoff(self, kind: str | None = None) -> None:
        """Forget failure history for one list kind (or all), re-enabling upstream attempts."""
        if kind is None:
            self._list_failures.clear()
        else:
            self._list_failures.pop(kind, None)

    def _check_backoff(self, kind: str) -> None:
        """Raise ``ProxyBackoffError`` when ``kind`` has given up or is inside its retry window."""
        state = self._list_failures.get(kind)
        if state is None:
            return
        failures, retry_at, last_error = state
        if self._backoff.exhausted(failures):
            raise ProxyBackoffError(
                f"upstream {kind} list gave up after {failures} failed attempts: {last_error}"
            )
        remaining = retry_at - time.monotonic()
        if remaining > 0:
            raise ProxyBackoffError(
                f"upstream {kind} list backing off {remaining:.1f}s "
                f"after {failures} failure(s): {last_error}"
            )

    def _record_failure(self, kind: str, exc: BaseException) -> None:
        """Bump ``kind``'s consecutive-failure count and schedule its next allowed attempt."""
        failures = self._list_failures.get(kind, (0, 0.0, ""))[0] + 1
        delay = self._backoff.delay(failures)
        self._list_failures[kind] = (failures, time.monotonic() + delay, str(exc)[:500])
        if self._backoff.exhausted(failures):
            logger.error(
                "Proxy upstream %s list failed %d times; giving up until reset: %s",
                kind, failures, exc,
            )
            if self._on_exhausted is not None:
                try:
                    self._on_exhausted(kind, failures, self._list_failures[kind][2])
                except Exception:
                    logger.exception("Proxy exhausted hook failed for %s list", kind)
        else:
            logger.warning(
                "Proxy upstream %s list failed (attempt %d); next retry in %.1fs: %s",
                kind, failures, delay, exc,
            )

    async def _coalesced(self, kind: str, fetch: Callable[[], Awaitable[Any]]) -> Any:
        """Return a fresh memo hit, else join (or start) the one in-flight upstream fetch."""
        hit = self._list_memo.get(kind)
        if hit is not None and time.monotonic() - hit[0] < self._list_ttl_s:
            return hit[1]
        task = self._list_inflight.get(kind)
        if task is None:
            self._check_backoff(kind)  # fail fast: no upstream session while backing off
            task = asyncio.ensure_future(fetch())
            self._list_inflight[kind] = task
            task.add_done_callback(lambda t, k=kind: self._finish_fetch(k, t))
        return await asyncio.shield(task)

    def _finish_fetch(self, kind: str, task: asyncio.Future) -> None:
        """Clear the in-flight slot; memoize only successful results."""
        if self._list_inflight.get(kind) is task:
            del self._list_inflight[kind]
        if task.cancelled():
            return
        exc = task.exception()  # also marks a failure retrieved if every waiter left
        if exc is None:
            self._list_memo[kind] = (time.monotonic(), task.result())
            self._list_failures.pop(kind, None)
        else:
            self._record_failure(kind, exc)

    async def _list_tools(self):
        """List upstream tools via the coalesced, TTL-memoized fetch."""
        return await self._coalesced("tools", super()._list_tools)

    async def _list_resources(self):
        """List upstream resources via the coalesced, TTL-memoized fetch."""
        return await self._coalesced("resources", super()._list_resources)

    async def _list_resource_templates(self):
        """List upstream resource templates via the coalesced, TTL-memoized fetch."""
        return await self._coalesced("resource_templates", super()._list_resource_templates)

    async def _list_prompts(self):
        """List upstream prompts via the coalesced, TTL-memoized fetch."""
        return await self._coalesced("prompts", super()._list_prompts)


def set_exhausted_hook(provider: Any, hook: ExhaustedHook | None) -> bool:
    """Attach ``hook`` to the list backoff and the session breaker inside a ``build_proxy`` handle.

    The hook's ``kind`` is a list kind (``tools``/``prompts``/...) or ``"session"``.

    Returns False (no-op) when the handle has none, e.g. a test double.
    """
    for inner in getattr(getattr(provider, "server", None), "providers", None) or ():
        if isinstance(inner, CoalescingProxyProvider):
            inner._on_exhausted = hook
            breaker = getattr(inner, "_session_breaker", None)
            if breaker is not None:
                breaker.on_exhausted = hook
            return True
    return False


def _install_coalescing(
    proxy_server: FastMCP, ttl_s: float, backoff: BackoffPolicy | None = None
) -> bool:
    """Upgrade the proxy server's ``ProxyProvider`` in place (shared with ProxyMetadataMiddleware)."""
    for provider in getattr(proxy_server, "providers", None) or ():
        if type(provider) is ProxyProvider:
            provider.__class__ = CoalescingProxyProvider
            provider._init_coalescing(ttl_s, backoff)
            return True
    logger.warning("build_proxy: no ProxyProvider found; upstream list coalescing disabled")
    return False


_relay_auth_cls: type | None = None


def _get_relay_auth_cls() -> type:
    """Build (once) and return the ``RelayAuth`` class, importing ``httpx2`` lazily.

    ``httpx2`` is only needed when a proxy actually authenticates, so keeping the
    import (and the ``httpx2.Auth`` subclass it requires) out of module import time
    lets ``core.proxy.compose`` -- and everything chaining through it -- import on a
    host Python without ``httpx2``.
    """
    global _relay_auth_cls
    if _relay_auth_cls is not None:
        return _relay_auth_cls

    import httpx2

    class RelayAuth(httpx2.Auth):
        """httpx2.Auth that injects a fresh token from ExternalOAuthRelay.

        FastMCP 4 / MCP SDK v2 construct the upstream client with ``httpx2``.
        An ``httpx.Auth`` subclass is rejected as ``Invalid "auth" argument``,
        which FastMCP's ProxyProvider swallows into a list_tools warning and
        mounts zero namespaced tools.

        A proxy is not a plugin, but gets a synthetic manifest to reuse
        ExternalOAuthRelay.
        """
        requires_response_body = False

        def __init__(self, plugin_id: str, provider: str = "upstream", auth_header: str | None = None):
            """Initialize RelayAuth with plugin id, provider, and custom auth header."""
            self.plugin_id = plugin_id
            self.provider = provider
            self.auth_header = auth_header or "Authorization"

        async def async_auth_flow(self, request: "httpx2.Request"):
            """Asynchronous HTTPX auth flow to inject credentials into the request header."""
            from core.context import oauth_relay
            if oauth_relay is None:
                logger.error("RelayAuth: ExternalOAuthRelay singleton not available in core.context")
                yield request
                return

            try:
                token = await oauth_relay.get_token(self.plugin_id, self.provider)
                if not token:
                    logger.error(
                        "RelayAuth: empty token for plugin=%s provider=%s",
                        self.plugin_id, self.provider,
                    )
                else:
                    logger.info(
                        "RelayAuth: attaching token plugin=%s header=%s token_len=%d",
                        self.plugin_id, self.auth_header, len(token),
                    )
                # Default to Authorization: Bearer <token>
                if self.auth_header.lower() == "authorization":
                    request.headers[self.auth_header] = f"Bearer {token}"
                else:
                    request.headers[self.auth_header] = token
            except Exception as e:
                logger.exception("RelayAuth: Failed to retrieve token from ExternalOAuthRelay: %s", e)
        
            yield request

    RelayAuth.__module__ = __name__
    _relay_auth_cls = RelayAuth
    return RelayAuth


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute: ``RelayAuth`` resolves to the cached ``httpx2.Auth`` subclass."""
    if name == "RelayAuth":
        return _get_relay_auth_cls()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_proxy(
    name: str,
    transport: str,
    url: str,
    auth_mode: str = "none",
    bearer_token: str | None = None,
    oauth_config: dict | None = None,
) -> FastMCPProvider:
    """Build a FastMCPProvider wrapping a proxy client to the upstream server."""
    logger.info(
        f"Building upstream proxy: name={name}, transport={transport}, url={url}, "
        f"auth_mode={auth_mode}, has_bearer={bool(bearer_token)}, has_oauth={bool(oauth_config)}"
    )
    
    auth_obj = None
    if auth_mode == "bearer" and bearer_token:
        auth_obj = bearer_token
    elif auth_mode == "oauth" and oauth_config:
        plugin_id = f"proxy_{name}"
        auth_header = oauth_config.get("auth_header") or "Authorization"
        auth_obj = _get_relay_auth_cls()(plugin_id=plugin_id, provider="upstream", auth_header=auth_header)

    max_sessions, list_ttl_s = _proxy_limits()
    transport_cls = (
        BoundedSSETransport
        if transport.lower() == "sse" or url.endswith("/sse")
        else BoundedStreamableHttpTransport
    )
    client_transport = transport_cls(
        url=url, auth=auth_obj, max_sessions=max_sessions, backoff=_proxy_backoff()
    )

    proxy_client = ProxyClient(client_transport, name=name)

    # create_proxy is the non-deprecated replacement for FastMCP.as_proxy().
    # Raise on upstream list_tools failure so discovery cannot "succeed" with
    # 0 tools (FastMCPProxy defaults to provider_error_strategy="warn").
    proxy_server = create_proxy(
        proxy_client, name=name, provider_error_strategy="raise"
    )
    _install_coalescing(proxy_server, list_ttl_s, _proxy_backoff())
    for inner in proxy_server.providers:
        if isinstance(inner, CoalescingProxyProvider):
            inner._session_breaker = client_transport._breaker  # reached by set_exhausted_hook

    provider = FastMCPProvider(proxy_server)
    return provider


def mount_proxy(mcp: FastMCP, provider: FastMCPProvider, name: str) -> None:
    """Mount the proxy provider onto the core MCP instance under the specified namespace."""
    logger.info(f"Mounting proxy under namespace: {name}")
    mcp.add_provider(provider, namespace=name)


def _find_mounted_provider(
    mcp: FastMCP,
    handle: FastMCPProvider | None,
    *,
    namespace: str | None = None,
):
    """Locate the live providers-list entry for a proxy handle or namespace."""
    providers = getattr(mcp, "providers", None)
    if not isinstance(providers, list):
        return None

    for entry in providers:
        if handle is not None and entry is handle:
            return entry
        inner = getattr(entry, "_inner", None)
        if handle is not None and inner is handle:
            return entry
        if namespace:
            for transform in getattr(entry, "transforms", ()) or ():
                prefix = getattr(transform, "_prefix", None)
                if prefix == namespace:
                    return entry
    return None


def unmount_proxy(
    mcp: FastMCP,
    handle: FastMCPProvider | None,
    *,
    namespace: str | None = None,
) -> None:
    """Best-effort live unmount of a proxy provider from the mcp instance.

    NOTE: FastMCP 3.3.1 has no public removal API (no remove_provider()).
    Direct list manipulation on mcp.providers is the only option.
    """
    logger.info(
        "Attempting live unmount of proxy provider: %s (namespace=%s)",
        handle, namespace,
    )
    if handle is None and not namespace:
        logger.warning("unmount_proxy: no handle or namespace provided.")
        return

    target = _find_mounted_provider(mcp, handle, namespace=namespace)
    providers = getattr(mcp, "providers", None)
    if not isinstance(providers, list):
        logger.error("mcp.providers is not accessible or not a list. Cannot perform live unmount.")
        return

    if target is not None:
        providers.remove(target)
        logger.info("Successfully removed proxy provider from live registry.")
    else:
        logger.warning("Proxy provider handle not found in live registry.")
