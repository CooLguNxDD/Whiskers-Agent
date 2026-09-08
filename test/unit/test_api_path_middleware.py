"""Unit tests for segment-based URL gating in the API path middleware."""

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from api.middleware import SessionGateMiddleware, PublicPathMiddleware


async def dummy_endpoint(request):
    return JSONResponse({
        "status": "ok",
        "auth_bypassed": request.scope.get("auth_bypassed", False)
    })


@pytest.fixture
def test_app(monkeypatch):
    class MockSvc:
        async def validate_token(self, token: str):
            if token == "valid_session":
                return
            raise ValueError("Invalid session token")

    class MockOAuthProvider:
        _svc = MockSvc()

    # Mock oauth_provider in api.middleware
    monkeypatch.setattr("api.middleware.oauth_provider", MockOAuthProvider())

    # Build a simple Starlette app with routes matching the patterns
    inner_app = Starlette()
    inner_app.add_route("/api/admin/public/login", dummy_endpoint, methods=["GET"])
    inner_app.add_route("/api/health/session_gated", dummy_endpoint, methods=["GET"])
    inner_app.add_route("/api/terminal/none/ws/x", dummy_endpoint, methods=["GET"])
    inner_app.add_route("/api/plugins/session_gated", dummy_endpoint, methods=["GET"])
    inner_app.add_route("/plugins", dummy_endpoint, methods=["GET"])

    app = Starlette()
    app.mount("/mcp", inner_app)
    app.mount("/", inner_app)

    # Wrap with middlewares in the same order as whiskers_agent_mcp.py:
    # 1. PublicPathMiddleware (inner/applied first)
    # 2. SessionGateMiddleware (outer/applied second)
    wrapped_app = PublicPathMiddleware(app)
    wrapped_app = SessionGateMiddleware(wrapped_app)
    
    return TestClient(wrapped_app)


def test_public_gate_segment_bypasses_session(test_app):
    # /api/admin/public/login without session -> passes through (not 401)
    response = test_app.get("/api/admin/public/login")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["auth_bypassed"] is True


def test_session_gated_segment_requires_session_401(test_app):
    # /api/health/session_gated without session -> 401
    response = test_app.get("/api/health/session_gated")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthenticated"}


def test_session_gated_segment_with_session_passes(test_app):
    # /api/health/session_gated with session -> passes through
    test_app.cookies.set("session", "valid_session")
    response = test_app.get("/api/health/session_gated")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["auth_bypassed"] is False


def test_none_gate_segment_bypasses_session(test_app):
    # /api/terminal/none/ws/x without session -> passes through
    response = test_app.get("/api/terminal/none/ws/x")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["auth_bypassed"] is False


def test_plugins_session_gated_requires_session_401(test_app):
    # /api/plugins/session_gated without session -> 401
    response = test_app.get("/api/plugins/session_gated")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthenticated"}


def test_non_api_gated_redirects(test_app):
    # Non-api route /plugins without session should redirect to /login?next=...
    response = test_app.get("/plugins", follow_redirects=False)
    assert response.status_code == 302
    assert "/login?next=%2Fplugins" in response.headers["location"]


def test_mcp_prefixed_routes(test_app):
    response = test_app.get("/mcp/api/admin/public/login")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["auth_bypassed"] is True

    response = test_app.get("/mcp/api/health/session_gated")
    assert response.status_code == 401
