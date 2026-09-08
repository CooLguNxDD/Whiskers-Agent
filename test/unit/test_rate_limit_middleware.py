import time
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.testclient import TestClient
from api.rate_limit_middleware import RateLimitMiddleware
from utils.server_config import _clamp_int


async def homepage(request):
    return JSONResponse({"status": "ok"})


def make_app(
    enabled=True,
    max_requests=3,
    window_seconds=10,
    path_prefixes=("/mcp", "/api/portfolio"),
    trust_proxy=False,
    max_tracked_ips=10000,
    prune_interval=60,
):
    from starlette.routing import Route
    app = Starlette(
        routes=[
            Route("/mcp", homepage, methods=["GET", "POST", "OPTIONS"]),
            Route("/api/portfolio", homepage, methods=["GET"]),
            Route("/api/portfolio/public/layout/{job_id}", homepage, methods=["GET"]),
        ]
    )
    # Wrap with our middleware
    app.add_middleware(
        RateLimitMiddleware,
        enabled=enabled,
        max_requests=max_requests,
        window_seconds=window_seconds,
        path_prefixes=path_prefixes,
        trust_proxy=trust_proxy,
        max_tracked_ips=max_tracked_ips,
        prune_interval=prune_interval,
    )
    return app


def test_allows_under_limit():
    app = make_app(max_requests=3, window_seconds=60)
    client = TestClient(app)

    # 3 allowed requests
    for _ in range(3):
        response = client.get("/mcp")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_blocks_over_limit():
    app = make_app(max_requests=3, window_seconds=60)
    client = TestClient(app)

    # 3 allowed requests
    for _ in range(3):
        client.get("/mcp")

    # 4th request gets blocked
    response = client.get("/mcp")
    assert response.status_code == 429
    data = response.json()
    assert data["error"] == "rate_limited"
    assert "retry_after" in data
    assert response.headers["Retry-After"] == str(data["retry_after"])
    assert data["retry_after"] > 0


def test_blocks_over_limit_on_public_job_layout_route():
    """The new /api/portfolio/public/layout/{job_id} route (Feature B) shares the /api/portfolio bucket."""
    app = make_app(max_requests=3, window_seconds=60)
    client = TestClient(app)

    for _ in range(3):
        response = client.get("/api/portfolio/public/layout/whiskers_successor_992")
        assert response.status_code == 200

    response = client.get("/api/portfolio/public/layout/whiskers_successor_992")
    assert response.status_code == 429


def test_independent_ips():
    app = make_app(max_requests=3, window_seconds=60, trust_proxy=True)
    client = TestClient(app)

    # IP 1 hits limit
    for _ in range(3):
        client.get("/mcp", headers={"X-Forwarded-For": "1.1.1.1"})
    assert client.get("/mcp", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429

    # IP 2 is still allowed
    assert client.get("/mcp", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200


def test_trust_proxy_disabled():
    app = make_app(max_requests=3, window_seconds=60, trust_proxy=False)
    client = TestClient(app)

    # Even with XFF header, standard client IP is used.
    # Starlette TestClient client IP defaults to 127.0.0.1 or testclient.
    for _ in range(3):
        client.get("/mcp", headers={"X-Forwarded-For": "1.1.1.1"})

    # This gets blocked because they map to the same client IP
    assert client.get("/mcp", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


def test_path_filtering():
    app = make_app(max_requests=3, window_seconds=60, path_prefixes=("/mcp",))
    client = TestClient(app)

    # Hit /mcp 4 times -> blocked
    for _ in range(3):
        client.get("/mcp")
    assert client.get("/mcp").status_code == 429

    # Hit /api/portfolio -> should be completely bypassed since prefix is not in allowlist
    assert client.get("/api/portfolio").status_code == 200


def test_disabled_middleware():
    app = make_app(enabled=False, max_requests=2)
    client = TestClient(app)

    for _ in range(5):
        assert client.get("/mcp").status_code == 200


def test_options_bypass():
    app = make_app(max_requests=2)
    client = TestClient(app)

    for _ in range(5):
        assert client.options("/mcp").status_code == 200


def test_pruning_and_expiry():
    # Setup middleware directly to test prune and timestamp expiry
    from api.rate_limit_middleware import RateLimitMiddleware

    mw = RateLimitMiddleware(
        app=None,
        enabled=True,
        max_requests=3,
        window_seconds=10,
        path_prefixes=("/mcp",),
        trust_proxy=False,
        max_tracked_ips=2,
        prune_interval=5,
    )

    # Fill hits
    mw._hits["1.1.1.1"] = [time.monotonic() - 15]  # Expired
    mw._hits["2.2.2.2"] = [time.monotonic()]  # Not expired

    # Manually trigger prune by overriding last prune timestamp
    mw._last_prune = time.monotonic() - 10
    mw._prune(time.monotonic())

    assert "1.1.1.1" not in mw._hits
    assert "2.2.2.2" in mw._hits

    # Test max tracked IPs capping
    mw._hits["3.3.3.3"] = [time.monotonic()]
    mw._hits["4.4.4.4"] = [time.monotonic()]
    mw._last_prune = time.monotonic() - 10
    mw._prune(time.monotonic())

    # Length capped at max_tracked_ips (2)
    assert len(mw._hits) <= 2


def test_clamp_int_validation():
    # Test _clamp_int helper derivation edge cases
    assert _clamp_int("garbage", 30, 1, 100) == 30
    assert _clamp_int(0, 30, 1, 100) == 1
    assert _clamp_int(500, 30, 1, 100) == 100
    assert _clamp_int(45, 30, 1, 100) == 45
