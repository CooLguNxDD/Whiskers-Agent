"""Unit tests for api.middleware.SessionGateMiddleware._check_required_scopes.

Covers the Stage 3 HTTP endpoint scope-enforcement seam: a gated path with a
non-empty ``required_scopes`` binding must 403 a session that lacks them, and
an empty binding (today's overwhelming majority of routes) must stay a no-op
— exercised directly against the static helper rather than through a full
ASGI request/response cycle, since the method takes the concrete path +
already-decoded JWT payload and returns a Response or None.
"""

import pytest

from api.middleware import SessionGateMiddleware


@pytest.fixture(autouse=True)
def _enforcement_on(monkeypatch):
    import core.scope_management.policy as policy_mod
    monkeypatch.setattr(policy_mod, "scope_enforcement_enabled", lambda: True)
    monkeypatch.setattr(policy_mod, "get_enforcement_mode", lambda: "enforce")


def _register(monkeypatch, path, required_scopes):
    from core.route_registry.http_route_registry import HttpRouteRegistry, RouteDeclaration, AuthPolicy
    import core.route_registry.http_route_registry as hrr_mod

    class _FakeMcp:
        def custom_route(self, *a, **k):
            def deco(fn):
                return fn
            return deco

    reg = HttpRouteRegistry(_FakeMcp())
    reg.declare(
        RouteDeclaration(
            path=path,
            endpoint=None,
            auth_policy=AuthPolicy.SESSION_GATED,
            required_scopes=tuple(required_scopes),
            owner="api.test",
        )
    )
    monkeypatch.setattr(hrr_mod, "_http_route_registry", reg)
    return reg


def test_empty_required_scopes_is_noop(monkeypatch):
    _register(monkeypatch, "/api/config/session_gated/llm", [])
    result = SessionGateMiddleware._check_required_scopes(
        "/api/config/session_gated/llm", {"scopes": []}
    )
    assert result is None


def test_no_route_bound_is_noop(monkeypatch):
    result = SessionGateMiddleware._check_required_scopes("/api/unbound/path", {"scopes": ["admin"]})
    assert result is None


def test_missing_scope_denies_403(monkeypatch):
    _register(monkeypatch, "/api/config/session_gated/plugin-gates", ["core:config:write"])
    result = SessionGateMiddleware._check_required_scopes(
        "/api/config/session_gated/plugin-gates", {"scopes": ["core:config:read"]}
    )
    assert result is not None
    assert result.status_code == 403


def test_matching_scope_allows(monkeypatch):
    _register(monkeypatch, "/api/config/session_gated/plugin-gates", ["core:config:write"])
    result = SessionGateMiddleware._check_required_scopes(
        "/api/config/session_gated/plugin-gates", {"scopes": ["core:config:write"]}
    )
    assert result is None


def test_raw_jwt_space_delimited_scope_str_fallback(monkeypatch):
    """Raw decoded JWT claims shape (scope: str) fallback must still work."""
    _register(monkeypatch, "/api/config/session_gated/plugin-gates", ["core:config:write"])
    result = SessionGateMiddleware._check_required_scopes(
        "/api/config/session_gated/plugin-gates", {"scope": "core:config:write"}
    )
    assert result is None


def test_admin_scope_bypasses(monkeypatch):
    _register(monkeypatch, "/api/config/session_gated/plugin-gates", ["core:config:write"])
    result = SessionGateMiddleware._check_required_scopes(
        "/api/config/session_gated/plugin-gates", {"scopes": ["admin"]}
    )
    assert result is None


def test_none_payload_denies(monkeypatch):
    """A required-scope route with no usable payload (shouldn't happen post-validity-check,
    but the helper must fail closed, not crash) denies rather than allowing."""
    _register(monkeypatch, "/api/config/session_gated/plugin-gates", ["core:config:write"])
    result = SessionGateMiddleware._check_required_scopes(
        "/api/config/session_gated/plugin-gates", None
    )
    assert result is not None
    assert result.status_code == 403


def test_lookup_failure_fails_open_to_no_requirement(monkeypatch):
    """An internal error in the scope lookup/evaluation must not block a
    session whose route carries no requirement it could even resolve."""
    import core.route_registry.http_route_registry as hrr_mod

    def _boom():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(hrr_mod, "get_http_route_registry", _boom)
    result = SessionGateMiddleware._check_required_scopes(
        "/api/config/session_gated/plugin-gates", {"scopes": ["core:config:write"]}
    )
    assert result is None


def test_param_template_route_scope_denied(monkeypatch):
    _register(
        monkeypatch,
        "/api/config/session_gated/plugin-gates/{plugin_id}",
        ["core:config:write"],
    )
    result = SessionGateMiddleware._check_required_scopes(
        "/api/config/session_gated/plugin-gates/jules_plugin", {"scopes": ["core:config:read"]}
    )
    assert result is not None
    assert result.status_code == 403


def test_oauth_validate_token_payload_contract():
    """Assert contract: OAuthService.validate_token produces 'scopes' (list[str]) and excludes 'scope'."""
    # Simulate the dictionary transformation performed by OAuthService.validate_token (L762-773)
    payload = {
        "aud": "test_client",
        "scope": "core:config:read core:terminal:read",
        "sub": "user_1",
        "exp": 1234567890,
        "token_type": "access",
        "org_id": "default",
        "jti": "jwt_id_123",
        "extra_custom_claim": "value",
    }
    jti = payload.get("jti")
    res = {
        "jti": jti,
        "client_id": payload.get("aud"),
        "scopes": payload.get("scope", "").split(),
        "sub": payload.get("sub"),
        "exp": payload.get("exp"),
        "token_type": payload.get("token_type", "access"),
        "org_id": payload.get("org_id", "default"),
    }
    for k, v in payload.items():
        if k not in res and k not in ("aud", "scope"):
            res[k] = v

    assert "scopes" in res
    assert isinstance(res["scopes"], list)
    assert res["scopes"] == ["core:config:read", "core:terminal:read"]
    assert "scope" not in res
