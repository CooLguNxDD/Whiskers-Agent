import logging
import time
from starlette.responses import JSONResponse

logger = logging.getLogger("whiskers")


class RateLimitMiddleware:
    """ASGI middleware for rate-limiting incoming requests by client IP.

    Uses a sliding-window algorithm stored in memory.
    """

    def __init__(
        self,
        app,
        enabled: bool,
        max_requests: int,
        window_seconds: int,
        path_prefixes: tuple[str, ...],
        trust_proxy: bool,
        max_tracked_ips: int,
        prune_interval: int = 60,
    ):
        self._app = app
        self._enabled = enabled
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._path_prefixes = path_prefixes
        self._trust_proxy = trust_proxy
        self._max_tracked_ips = max_tracked_ips
        self._prune_interval = prune_interval

        self._hits: dict[str, list[float]] = {}
        self._last_prune: float = time.monotonic()

    def _client_ip(self, scope) -> str:
        """Extract the client IP address based on trust_proxy configuration."""
        if self._trust_proxy:
            # Check X-Forwarded-For header
            headers = scope.get("headers", [])
            for name, value in headers:
                if name.lower() == b"x-forwarded-for":
                    try:
                        xff = value.decode("utf-8", errors="ignore")
                        if xff:
                            return xff.split(",")[0].strip()
                    except Exception:
                        logger.debug("rate_limit_middleware.py: swallowed exception", exc_info=True)
                    break

        client = scope.get("client")
        if client and isinstance(client, (list, tuple)) and len(client) > 0:
            return str(client[0])

        return "unknown"

    def _prune(self, now: float) -> None:
        """Remove stale request records and enforce memory limits."""
        if now - self._last_prune < self._prune_interval:
            return

        window_start = now - self._window_seconds
        expired_ips = []
        for ip, attempts in list(self._hits.items()):
            recent = [t for t in attempts if t > window_start]
            if recent:
                self._hits[ip] = recent
            else:
                expired_ips.append(ip)

        for ip in expired_ips:
            self._hits.pop(ip, None)

        if len(self._hits) > self._max_tracked_ips:
            excess = len(self._hits) - self._max_tracked_ips
            for ip in list(self._hits.keys())[:excess]:
                self._hits.pop(ip, None)

        self._last_prune = now

    async def __call__(self, scope, receive, send):
        # 1. Skip non-HTTP requests
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        # 2. Skip when disabled
        if not self._enabled:
            await self._app(scope, receive, send)
            return

        # 3. Skip OPTIONS preflight requests
        if scope.get("method") == "OPTIONS":
            await self._app(scope, receive, send)
            return

        # 4. Skip paths not matching prefixes
        path = scope.get("path", "")
        if not any(path.startswith(prefix) for prefix in self._path_prefixes):
            await self._app(scope, receive, send)
            return

        # 5. Extract IP and evaluate rate limit
        ip = self._client_ip(scope)
        now = time.monotonic()
        self._prune(now)

        attempts = self._hits.get(ip, [])
        window_start = now - self._window_seconds
        recent_attempts = [t for t in attempts if t > window_start]

        if len(recent_attempts) >= self._max_requests:
            oldest = recent_attempts[0]
            retry_after = int(self._window_seconds - (now - oldest)) + 1
            if retry_after < 1:
                retry_after = 1

            response = JSONResponse(
                {"error": "rate_limited", "retry_after": retry_after},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        # Record this request
        recent_attempts.append(now)
        self._hits[ip] = recent_attempts

        await self._app(scope, receive, send)
