"""FastMCP composition logic for Whiskers Agent upstream proxies.

Memory bounding (FastMCP opens a *fresh* upstream session per proxied request —
initialize + GET stream + call + DELETE, each with its own ``httpx2.AsyncClient``):

- every session's client reuses one shared TLS context instead of re-loading the
  CA bundle (~1.4 MB + CPU per client);
- a per-proxy semaphore caps concurrently open upstream sessions so agent fan-out
  queues instead of stacking hundreds of live sessions;
- ``tools``/``prompts``/``resources`` listings are coalesced (concurrent misses
  share one upstream fetch) and served from a short TTL memo, so neither a burst
  of front ``tools/list`` calls nor ``ProxyProvider``'s 300s lookup-cache expiry
  re-downloads the upstream catalog once per caller.
"""

import asyncio
import contextlib
import logging
import ssl
import time
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


def _proxy_limits() -> tuple[int, float]:
    """Return ``(max_concurrent_sessions, list_cache_ttl_s)`` from ``graph`` config, with defaults."""
    sessions, ttl = _DEFAULT_MAX_CONCURRENT_SESSIONS, _DEFAULT_LIST_CACHE_TTL_S
    try:
        from utils.config_registry import get_config_registry
        graph = get_config_registry().server.get("graph", {}) or {}
        sessions = max(1, int(graph.get("proxy_max_concurrent_sessions", sessions)))
        ttl = max(0.0, float(graph.get("proxy_list_cache_ttl_s", ttl)))
    except Exception:
        logger.debug("proxy limits config unavailable; using defaults", exc_info=True)
    return sessions, ttl


_shared_ssl_ctx: ssl.SSLContext | None = None


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
    return httpx2.AsyncClient(
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=follow_redirects,
        verify=_shared_ssl_ctx,
    )


class _SessionGateMixin:
    """Caps concurrently open upstream sessions per proxy (``Client.new()`` shares the transport)."""

    _session_gate: asyncio.Semaphore

    @contextlib.asynccontextmanager
    async def connect_session(self, **kwargs: Any):
        """Hold a gate slot for the whole upstream session lifetime."""
        async with self._session_gate:
            async with super().connect_session(**kwargs) as session:  # type: ignore[misc]
                yield session


class BoundedStreamableHttpTransport(_SessionGateMixin, StreamableHttpTransport):
    """``StreamableHttpTransport`` with a per-proxy open-session cap and shared TLS context."""

    def __init__(self, url: str, auth: Any = None, *, max_sessions: int = _DEFAULT_MAX_CONCURRENT_SESSIONS):
        super().__init__(url=url, auth=auth, httpx_client_factory=_shared_http_client_factory)
        self._session_gate = asyncio.Semaphore(max_sessions)


class BoundedSSETransport(_SessionGateMixin, SSETransport):
    """``SSETransport`` with a per-proxy open-session cap and shared TLS context."""

    def __init__(self, url: str, auth: Any = None, *, max_sessions: int = _DEFAULT_MAX_CONCURRENT_SESSIONS):
        super().__init__(url=url, auth=auth, httpx_client_factory=_shared_http_client_factory)
        self._session_gate = asyncio.Semaphore(max_sessions)


class CoalescingProxyProvider(ProxyProvider):
    """``ProxyProvider`` whose list fetches are single-flight and briefly memoized.

    Errors are never memoized; a waiter's cancellation never cancels the shared fetch.
    """

    _list_ttl_s: float
    _list_memo: dict[str, tuple[float, Any]]
    _list_inflight: dict[str, asyncio.Future]

    def _init_coalescing(self, ttl_s: float) -> None:
        """Initialize memo state (called after the in-place class swap in ``build_proxy``)."""
        self._list_ttl_s = ttl_s
        self._list_memo = {}
        self._list_inflight = {}

    async def _coalesced(self, kind: str, fetch: Callable[[], Awaitable[Any]]) -> Any:
        """Return a fresh memo hit, else join (or start) the one in-flight upstream fetch."""
        hit = self._list_memo.get(kind)
        if hit is not None and time.monotonic() - hit[0] < self._list_ttl_s:
            return hit[1]
        task = self._list_inflight.get(kind)
        if task is None:
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
        if task.exception() is None:  # also marks a failure retrieved if every waiter left
            self._list_memo[kind] = (time.monotonic(), task.result())

    async def _list_tools(self):
        return await self._coalesced("tools", super()._list_tools)

    async def _list_resources(self):
        return await self._coalesced("resources", super()._list_resources)

    async def _list_resource_templates(self):
        return await self._coalesced("resource_templates", super()._list_resource_templates)

    async def _list_prompts(self):
        return await self._coalesced("prompts", super()._list_prompts)


def _install_coalescing(proxy_server: FastMCP, ttl_s: float) -> bool:
    """Upgrade the proxy server's ``ProxyProvider`` in place (shared with ProxyMetadataMiddleware)."""
    for provider in getattr(proxy_server, "providers", None) or ():
        if type(provider) is ProxyProvider:
            provider.__class__ = CoalescingProxyProvider
            provider._init_coalescing(ttl_s)
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
    if transport.lower() == "sse" or url.endswith("/sse"):
        client_transport = BoundedSSETransport(url=url, auth=auth_obj, max_sessions=max_sessions)
    else:
        client_transport = BoundedStreamableHttpTransport(url=url, auth=auth_obj, max_sessions=max_sessions)

    proxy_client = ProxyClient(client_transport, name=name)

    # create_proxy is the non-deprecated replacement for FastMCP.as_proxy().
    # Raise on upstream list_tools failure so discovery cannot "succeed" with
    # 0 tools (FastMCPProxy defaults to provider_error_strategy="warn").
    proxy_server = create_proxy(
        proxy_client, name=name, provider_error_strategy="raise"
    )
    _install_coalescing(proxy_server, list_ttl_s)

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
