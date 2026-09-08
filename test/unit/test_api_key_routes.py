"""Unit tests for api_key_routes.py.

Exercises the API key login and management endpoints using a throwaway
Starlette app and TestClient.
"""

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from api.api_key_routes import (
    admin_login_api_key,
    api_list_api_keys,
    api_create_api_key,
    api_revoke_api_key,
    api_delete_api_key,
    api_update_api_key_scopes,
    api_list_scope_presets,
    api_create_scope_preset,
    api_delete_scope_preset,
    api_get_scope_vocabulary,
)

routes = [
    Route("/api/admin/public/login-api-key", admin_login_api_key, methods=["POST"]),
    Route("/api/auth/session_gated/api-keys", api_list_api_keys, methods=["GET"]),
    Route("/api/auth/session_gated/api-keys", api_create_api_key, methods=["POST"]),
    Route("/api/auth/session_gated/api-keys/{key_id}/revoke", api_revoke_api_key, methods=["POST"]),
    Route("/api/auth/session_gated/api-keys/{key_id}", api_delete_api_key, methods=["DELETE"]),
    Route("/api/auth/session_gated/api-keys/{key_id}/scopes", api_update_api_key_scopes, methods=["PUT"]),
    Route("/api/auth/session_gated/api-key-presets", api_list_scope_presets, methods=["GET"]),
    Route("/api/auth/session_gated/api-key-presets", api_create_scope_preset, methods=["POST"]),
    Route("/api/auth/session_gated/api-key-presets/{preset_id}", api_delete_scope_preset, methods=["DELETE"]),
    Route("/api/auth/session_gated/scope-vocabulary", api_get_scope_vocabulary, methods=["GET"]),
]


app = Starlette(routes=routes)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    import api.admin_routes as ar
    ar._login_attempts.clear()
    ar._last_rate_limit_prune = 0.0
    yield
    ar._login_attempts.clear()


@pytest.fixture(autouse=True)
def default_tenant_id(monkeypatch):
    async def mock_resolve_tenant_id(request):
        session_token = request.cookies.get("session")
        if session_token in ("unauthenticated_session", "missing_tenant"):
            return None
        return 1

    monkeypatch.setattr("api.api_key_routes._resolve_tenant_id", mock_resolve_tenant_id)



def test_login_api_key_invalid_json():
    client = TestClient(app)
    response = client.post("/api/admin/public/login-api-key", content="invalid json")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"


def test_login_api_key_invalid_json_type():
    client = TestClient(app)
    response = client.post("/api/admin/public/login-api-key", json=[1, 2, 3])
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"


def test_login_api_key_missing_key(monkeypatch):
    async def mock_lookup(token):
        return None

    monkeypatch.setattr("api.api_key_routes.lookup_active_by_token", mock_lookup)

    client = TestClient(app)
    response = client.post("/api/admin/public/login-api-key", json={"state": "hello"})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_api_key"


def test_login_api_key_bad_key(monkeypatch):
    async def mock_lookup(token):
        return None

    monkeypatch.setattr("api.api_key_routes.lookup_active_by_token", mock_lookup)

    client = TestClient(app)
    response = client.post("/api/admin/public/login-api-key", json={"api_key": "bad_token"})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_api_key"


def test_login_api_key_valid_key(monkeypatch):
    async def mock_lookup(token):
        assert token == "octk_good_token"
        return {
            "key_id": "ak_123",
            "subject": "admin",
            "expires_at": None,
            "scopes": ["all"],
            "tenant_id": 1,
        }

    monkeypatch.setattr("api.api_key_routes.lookup_active_by_token", mock_lookup)

    mock_oauth_service = MagicMock()
    mock_oauth_service.ensure_internal_client = AsyncMock()
    mock_oauth_service._issue_token_pair = AsyncMock(
        return_value={"access_token": "mock_access", "refresh_token": "mock_refresh"}
    )

    def mock_get_oauth_service():
        return mock_oauth_service

    monkeypatch.setattr("api.api_key_routes._get_oauth_service", mock_get_oauth_service)

    client = TestClient(app)
    response = client.post(
        "/api/admin/public/login-api-key",
        json={"api_key": "octk_good_token", "state": "my_state", "next": "/admin/settings"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["redirect"] == "/?state=my_state"
    assert "session" in response.cookies
    assert "refresh" in response.cookies
    assert response.cookies["session"] == "mock_access"
    assert response.cookies["refresh"] == "mock_refresh"
    # full-access key mints admin session
    call_kwargs = mock_oauth_service._issue_token_pair.await_args.kwargs
    assert call_kwargs["scopes"] == ["admin"]


def test_login_api_key_uses_key_scopes_not_admin(monkeypatch):
    """Read-only API keys must not escalate to admin session scopes."""
    async def mock_lookup(token):
        return {
            "key_id": "ak_ro",
            "subject": "user-1",
            "expires_at": None,
            "scopes": ["plugin:fake_plugin"],
            "tenant_id": 2,
        }

    monkeypatch.setattr("api.api_key_routes.lookup_active_by_token", mock_lookup)

    mock_oauth_service = MagicMock()
    mock_oauth_service.ensure_internal_client = AsyncMock()
    mock_oauth_service._issue_token_pair = AsyncMock(
        return_value={"access_token": "a", "refresh_token": "r"}
    )
    monkeypatch.setattr("api.api_key_routes._get_oauth_service", lambda: mock_oauth_service)

    client = TestClient(app)
    response = client.post(
        "/api/admin/public/login-api-key",
        json={"api_key": "octk_ro_token"},
    )
    assert response.status_code == 200
    call_kwargs = mock_oauth_service._issue_token_pair.await_args.kwargs
    assert call_kwargs["scopes"] == ["plugin:fake_plugin"]
    assert call_kwargs["extra_claims"]["ocat_tenant"] == 2


def test_login_api_key_empty_scopes_refused(monkeypatch):
    async def mock_lookup(token):
        return {
            "key_id": "ak_empty",
            "subject": "admin",
            "expires_at": None,
            "scopes": [],
            "tenant_id": 1,
        }

    monkeypatch.setattr("api.api_key_routes.lookup_active_by_token", mock_lookup)
    mock_oauth_service = MagicMock()
    mock_oauth_service.ensure_internal_client = AsyncMock()
    monkeypatch.setattr("api.api_key_routes._get_oauth_service", lambda: mock_oauth_service)

    client = TestClient(app)
    response = client.post(
        "/api/admin/public/login-api-key",
        json={"api_key": "octk_empty"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "api_key_no_scopes"
    mock_oauth_service._issue_token_pair.assert_not_called()


def test_login_api_key_valid_key_oauth_unavailable(monkeypatch):
    async def mock_lookup(token):
        return {
            "key_id": "ak_123",
            "subject": "admin",
            "expires_at": None,
            "scopes": ["all"],
        }

    monkeypatch.setattr("api.api_key_routes.lookup_active_by_token", mock_lookup)

    def mock_get_oauth_service():
        return None

    monkeypatch.setattr("api.api_key_routes._get_oauth_service", mock_get_oauth_service)

    client = TestClient(app)
    response = client.post("/api/admin/public/login-api-key", json={"api_key": "octk_good_token"})
    assert response.status_code == 503
    assert response.json()["error"] == "oauth_service_unavailable"


def test_login_api_key_rate_limit(monkeypatch):
    async def mock_lookup(token):
        return None

    monkeypatch.setattr("api.api_key_routes.lookup_active_by_token", mock_lookup)

    client = TestClient(app)

    # 5 attempts allowed, each returns 401
    for _ in range(5):
        response = client.post("/api/admin/public/login-api-key", json={"api_key": "bad"})
        assert response.status_code == 401

    # 6th attempt is rate-limited
    response = client.post("/api/admin/public/login-api-key", json={"api_key": "bad"})
    assert response.status_code == 429
    assert response.json()["error"] == "too_many_attempts"
    assert "Retry-After" in response.headers


def test_list_api_keys(monkeypatch):
    now_dt = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)

    async def mock_list(subject, tenant_id=None):
        assert subject == "admin"
        return [
            {
                "key_id": "ak_1",
                "name": "Key One",
                "prefix": "octk_12345",
                "status": "active",
                "expires_at": now_dt,
                "last_used_at": None,
                "created_at": now_dt,
                "revoked_at": None,
                "scopes": [],
            }
        ]

    monkeypatch.setattr("api.api_key_routes.list_api_keys", mock_list)

    client = TestClient(app)
    response = client.get("/api/auth/session_gated/api-keys")
    assert response.status_code == 200
    data = response.json()
    assert "keys" in data
    assert len(data["keys"]) == 1
    key = data["keys"][0]
    assert key["key_id"] == "ak_1"
    assert key["name"] == "Key One"
    assert key["prefix"] == "octk_12345"
    assert key["status"] == "active"
    assert key["expires_at"] == "2026-06-20T12:00:00+00:00"
    assert key["last_used_at"] is None
    assert key["created_at"] == "2026-06-20T12:00:00+00:00"
    assert key["revoked_at"] is None
    assert key["scopes"] == []


def test_create_api_key_missing_name():
    client = TestClient(app)
    response = client.post("/api/auth/session_gated/api-keys", json={})
    assert response.status_code == 400

    response = client.post("/api/auth/session_gated/api-keys", json={"name": ""})
    assert response.status_code == 400

    response = client.post("/api/auth/session_gated/api-keys", json={"name": "   "})
    assert response.status_code == 400


def test_create_api_key_success(monkeypatch):
    async def mock_create(subject, name, expires_at, scopes=None, tenant_id=None):
        assert subject == "admin"
        assert name == "My Key"
        assert expires_at is None
        assert scopes == []
        return {
            "key_id": "ak_created",
            "token": "octk_secret_token_123",
            "prefix": "octk_secre",
            "name": name,
            "expires_at": expires_at,
            "scopes": scopes,
        }

    monkeypatch.setattr("api.api_key_routes.create_api_key", mock_create)

    client = TestClient(app)
    response = client.post("/api/auth/session_gated/api-keys", json={"name": "My Key"})
    assert response.status_code == 200
    data = response.json()
    assert data["key_id"] == "ak_created"
    assert data["token"] == "octk_secret_token_123"
    assert data["prefix"] == "octk_secre"
    assert data["name"] == "My Key"
    assert data["expires_at"] is None
    assert data["scopes"] == []


def test_create_api_key_with_expiry(monkeypatch):
    async def mock_create(subject, name, expires_at, scopes=None, tenant_id=None):
        assert subject == "admin"
        assert name == "My Expiring Key"
        assert expires_at is not None
        return {
            "key_id": "ak_expiring",
            "token": "octk_expiry_token_123",
            "prefix": "octk_expir",
            "name": name,
            "expires_at": expires_at,
            "scopes": scopes,
        }

    monkeypatch.setattr("api.api_key_routes.create_api_key", mock_create)

    client = TestClient(app)
    response = client.post("/api/auth/session_gated/api-keys", json={"name": "My Expiring Key", "expires_in": 3600})
    assert response.status_code == 200
    data = response.json()
    assert data["key_id"] == "ak_expiring"
    assert data["token"] == "octk_expiry_token_123"
    assert data["prefix"] == "octk_expir"
    assert data["name"] == "My Expiring Key"
    assert data["expires_at"] is not None
    assert data["scopes"] == []
    # Verify it parsed as datetime and serialized back to ISO string
    datetime.fromisoformat(data["expires_at"])


def test_create_api_key_with_scopes(monkeypatch):
    import utils.server_config
    monkeypatch.setattr(
        utils.server_config,
        "OAUTH_VALID_SCOPES",
        utils.server_config.OAUTH_VALID_SCOPES + ["plugin:foo"]
    )

    async def mock_create(subject, name, expires_at, scopes=None, tenant_id=None):
        assert subject == "admin"
        assert scopes == ["plugin:foo", "terminal:use"]
        return {
            "key_id": "ak_scoped",
            "token": "octk_scoped_token",
            "prefix": "octk_scope",
            "name": name,
            "expires_at": expires_at,
            "scopes": scopes,
        }

    monkeypatch.setattr("api.api_key_routes.create_api_key", mock_create)

    client = TestClient(app)
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "Scoped Key", "scopes": ["plugin:foo", "terminal:use"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scopes"] == ["plugin:foo", "terminal:use"]


def test_create_api_key_invalid_scopes():
    client = TestClient(app)
    response = client.post("/api/auth/session_gated/api-keys", json={"name": "Bad Scopes", "scopes": "not_a_list"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scopes"

    response = client.post("/api/auth/session_gated/api-keys", json={"name": "Bad Scopes", "scopes": [1, 2]})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scopes"


def test_create_api_key_invalid_expiry():
    client = TestClient(app)
    response = client.post("/api/auth/session_gated/api-keys", json={"name": "Bad Expiry", "expires_in": -10})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_expires_in"

    response = client.post("/api/auth/session_gated/api-keys", json={"name": "Bad Expiry", "expires_in": "not_an_int"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_expires_in"


def test_revoke_api_key(monkeypatch):
    async def mock_rotate(subject, key_id, tenant_id=None):
        assert subject == "admin"
        if key_id == "ak_exists":
            return {
                "key_id": "ak_new",
                "token": "octk_new_secret_123",
                "prefix": "octk_new_se",
                "name": "rotated",
                "expires_at": None,
            }
        return None

    monkeypatch.setattr("api.api_key_routes.rotate_api_key", mock_rotate)

    client = TestClient(app)

    response = client.post("/api/auth/session_gated/api-keys/ak_exists/revoke")
    assert response.status_code == 200
    data = response.json()
    assert data["key_id"] == "ak_new"
    assert data["token"].startswith("octk_")
    assert data["name"] == "rotated"

    response = client.post("/api/auth/session_gated/api-keys/ak_missing/revoke")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_delete_api_key(monkeypatch):
    async def mock_delete(subject, key_id, tenant_id=None):
        assert subject == "admin"
        if key_id == "ak_exists":
            return True
        return False

    monkeypatch.setattr("api.api_key_routes.delete_api_key", mock_delete)

    client = TestClient(app)

    response = client.delete("/api/auth/session_gated/api-keys/ak_exists")
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    response = client.delete("/api/auth/session_gated/api-keys/ak_missing")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_update_api_key_scopes(monkeypatch):
    import utils.server_config
    monkeypatch.setattr(
        utils.server_config,
        "OAUTH_VALID_SCOPES",
        utils.server_config.OAUTH_VALID_SCOPES + ["group:fake_plugin:status"]
    )

    async def mock_update(subject, key_id, scopes, tenant_id=None):
        assert subject == "admin"
        assert scopes == ["terminal:use", "group:fake_plugin:status"]
        if key_id == "ak_exists":
            return True
        return False

    monkeypatch.setattr("api.api_key_routes.update_api_key_scopes", mock_update)

    client = TestClient(app)

    response = client.put(
        "/api/auth/session_gated/api-keys/ak_exists/scopes",
        json={"scopes": ["terminal:use", "group:fake_plugin:status"]},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    response = client.put(
        "/api/auth/session_gated/api-keys/ak_missing/scopes",
        json={"scopes": ["terminal:use", "group:fake_plugin:status"]},
    )
    assert response.status_code == 404

    response = client.put(
        "/api/auth/session_gated/api-keys/ak_exists/scopes",
        json={"scopes": "not_a_list"},
    )
    assert response.status_code == 400


def test_update_api_key_scopes_missing_field_rejected(monkeypatch):
    async def mock_update(subject, key_id, scopes):
        raise AssertionError("update_api_key_scopes should not be called when 'scopes' is missing")

    monkeypatch.setattr("api.api_key_routes.update_api_key_scopes", mock_update)

    client = TestClient(app)

    response = client.put("/api/auth/session_gated/api-keys/ak_exists/scopes", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "scopes_required"


def test_list_scope_presets(monkeypatch):
    now_dt = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)

    async def mock_list(subject, tenant_id=None):
        assert subject == "admin"
        return [
            {
                "id": "preset_1",
                "name": "Preset One",
                "scopes": ["terminal:use"],
                "created_at": now_dt,
            }
        ]

    monkeypatch.setattr("api.api_key_routes.list_scope_presets", mock_list)

    client = TestClient(app)
    response = client.get("/api/auth/session_gated/api-key-presets")
    assert response.status_code == 200
    data = response.json()
    assert "presets" in data
    assert len(data["presets"]) == 1
    preset = data["presets"][0]
    assert preset["id"] == "preset_1"
    assert preset["name"] == "Preset One"
    assert preset["scopes"] == ["terminal:use"]
    assert preset["created_at"] == "2026-06-20T12:00:00+00:00"


def test_create_scope_preset_success(monkeypatch):
    import utils.server_config
    monkeypatch.setattr(
        utils.server_config,
        "OAUTH_VALID_SCOPES",
        utils.server_config.OAUTH_VALID_SCOPES + ["plugin:foo"]
    )

    now_dt = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)

    async def mock_create(subject, name, scopes):
        assert subject == "admin"
        assert name == "My Preset"
        assert scopes == ["terminal:use", "plugin:foo"]
        return {
            "id": "preset_new",
            "name": name,
            "scopes": scopes,
            "created_at": now_dt,
        }

    monkeypatch.setattr("api.api_key_routes.create_scope_preset", mock_create)

    client = TestClient(app)
    response = client.post(
        "/api/auth/session_gated/api-key-presets",
        json={"name": "My Preset", "scopes": ["terminal:use", "plugin:foo"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "preset_new"
    assert data["name"] == "My Preset"
    assert data["scopes"] == ["terminal:use", "plugin:foo"]
    assert data["created_at"] == "2026-06-20T12:00:00+00:00"


def test_create_scope_preset_missing_name():
    client = TestClient(app)
    response = client.post("/api/auth/session_gated/api-key-presets", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "name_required"

    response = client.post("/api/auth/session_gated/api-key-presets", json={"name": ""})
    assert response.status_code == 400
    assert response.json()["error"] == "name_required"

    response = client.post("/api/auth/session_gated/api-key-presets", json={"name": "   "})
    assert response.status_code == 400
    assert response.json()["error"] == "name_required"


def test_create_scope_preset_invalid_scopes():
    client = TestClient(app)
    response = client.post(
        "/api/auth/session_gated/api-key-presets",
        json={"name": "Preset", "scopes": "not_a_list"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scopes"

    response = client.post(
        "/api/auth/session_gated/api-key-presets",
        json={"name": "Preset", "scopes": [1, 2]},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scopes"


def test_delete_scope_preset_success(monkeypatch):
    async def mock_delete(subject, preset_id):
        assert subject == "admin"
        if preset_id == "preset_exists":
            return True
        return False

    monkeypatch.setattr("api.api_key_routes.delete_scope_preset", mock_delete)

    client = TestClient(app)
    response = client.delete("/api/auth/session_gated/api-key-presets/preset_exists")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_delete_scope_preset_not_found(monkeypatch):
    async def mock_delete(subject, preset_id):
        assert subject == "admin"
        return False

    monkeypatch.setattr("api.api_key_routes.delete_scope_preset", mock_delete)

    client = TestClient(app)
    response = client.delete("/api/auth/session_gated/api-key-presets/preset_missing")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_list_api_keys_with_pagination_and_filters(monkeypatch):
    now_dt = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
    other_dt = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)

    async def mock_list(subject, tenant_id=None):
        return [
            {
                "key_id": "ak_1",
                "name": "Alpha Key",
                "prefix": "octk_aaaaa",
                "status": "active",
                "expires_at": now_dt,
                "last_used_at": now_dt,
                "created_at": now_dt,
                "revoked_at": None,
                "scopes": [],
            },
            {
                "key_id": "ak_2",
                "name": "Beta Key",
                "prefix": "octk_bbbbb",
                "status": "revoked",
                "expires_at": None,
                "last_used_at": other_dt,
                "created_at": other_dt,
                "revoked_at": other_dt,
                "scopes": [],
            }
        ]

    monkeypatch.setattr("api.api_key_routes.list_api_keys", mock_list)

    client = TestClient(app)

    # 1. Test filtering by query
    res = client.get("/api/auth/session_gated/api-keys?query=alpha")
    assert res.status_code == 200
    data = res.json()
    assert len(data["keys"]) == 1
    assert data["keys"][0]["key_id"] == "ak_1"

    # 2. Test filtering by status
    res = client.get("/api/auth/session_gated/api-keys?status=revoked")
    assert res.status_code == 200
    data = res.json()
    assert len(data["keys"]) == 1
    assert data["keys"][0]["key_id"] == "ak_2"

    # 3. Test sorting
    res = client.get("/api/auth/session_gated/api-keys?sort=name")
    assert res.status_code == 200
    data = res.json()
    assert data["keys"][0]["name"] == "Alpha Key"
    assert data["keys"][1]["name"] == "Beta Key"

    # 4. Test pagination
    res = client.get("/api/auth/session_gated/api-keys?page=1&per_page=1")
    assert res.status_code == 200
    data = res.json()
    assert len(data["keys"]) == 1
    assert data["total"] == 2
    assert data["page"] == 1
    assert data["per_page"] == 1
    assert data["pages"] == 2


def test_get_scope_vocabulary():
    from core.plugin_loader.scope_registry import set_plugin_scopes, clear
    clear()
    set_plugin_scopes("testplug", [{"token": "plugin:testplug", "description": "Test plugin scope"}])
    try:
        client = TestClient(app)
        response = client.get("/api/auth/session_gated/scope-vocabulary")
        assert response.status_code == 200
        data = response.json()
        assert "global_scopes" in data
        assert isinstance(data["global_scopes"], list)
        # The default configuration-driven scopes list contains at least "whiskers"
        assert len(data["global_scopes"]) > 0

        assert "plugin_scopes" in data
        assert isinstance(data["plugin_scopes"], list)
        # The reserved "core" namespace survives clear() (see
        # unregister_plugin_permissions' docstring) so it may also be
        # present here; this test only cares about the registered plugin.
        plugin_scopes = [s for s in data["plugin_scopes"] if s["plugin_id"] != "core"]
        assert len(plugin_scopes) == 1
        assert plugin_scopes[0]["token"] == "plugin:testplug"
        assert plugin_scopes[0]["description"] == "Test plugin scope"
        assert plugin_scopes[0]["plugin_id"] == "testplug"
    finally:
        clear()


def test_create_api_key_role_checking(monkeypatch):
    """Test API key creation scope validation based on issuer's role."""
    from core.plugin_loader.scope_registry import set_plugin_scopes, clear

    # Operator include_data_plugins draws from the live vocab; seed a plugin token.
    clear()
    set_plugin_scopes(
        "jules_plugin",
        [{"token": "plugin:jules_plugin", "description": "Jules plugin"}],
    )

    # Mock create_api_key
    async def mock_create(subject, name, expires_at, scopes=None, tenant_id=None):
        return {
            "key_id": "ak_scoped",
            "token": "octk_scoped_token",
            "prefix": "octk_scope",
            "name": name,
            "expires_at": expires_at,
            "scopes": scopes,
        }
    monkeypatch.setattr("api.api_key_routes.create_api_key", mock_create)

    # Mock OAuth service
    mock_oauth = MagicMock()
    mock_oauth.validate_token = AsyncMock()
    monkeypatch.setattr("api.api_key_routes._get_oauth_service", lambda: mock_oauth)

    client = TestClient(app)
    try:
        _run_create_api_key_role_cases(client, mock_oauth)
    finally:
        clear()


def _run_create_api_key_role_cases(client, mock_oauth):
    """Role-gate matrix for create_api_key (shared body for fixture teardown)."""

    # 1. Master role session -> success
    mock_oauth.validate_token.return_value = {
        "sub": "admin",
        "ocat_role": "master",
    }
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "My Key", "scopes": ["terminal:use"]},
        cookies={"session": "master_cookie"},
    )
    assert response.status_code == 200

    # 2. Operator role session trying terminal:use -> 403 Forbidden with scopes_exceed_issuer
    mock_oauth.validate_token.return_value = {
        "sub": "operator",
        "ocat_role": "operator",
    }
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "My Key", "scopes": ["terminal:use"]},
        cookies={"session": "operator_cookie"},
    )
    assert response.status_code == 403
    data = response.json()
    assert data["error"] == "scopes_exceed_issuer"
    assert "terminal:use" in data["excess"]

    # 3. Operator role session trying allowed data scopes (e.g., plugin:jules_plugin) -> success
    mock_oauth.validate_token.return_value = {
        "sub": "operator",
        "ocat_role": "operator",
    }
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "My Key", "scopes": ["plugin:jules_plugin"]},
        cookies={"session": "operator_cookie"},
    )
    assert response.status_code == 200

    # 4. Session without an ocat_role claim -> fails closed to the lowest-
    # privilege role, NOT master (the escalation this used to permit: a
    # narrow API-key login with no claim stamped could mint terminal:use).
    mock_oauth.validate_token.return_value = {
        "sub": "legacy-admin",
    }
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "My Key", "scopes": ["terminal:use"]},
        cookies={"session": "legacy_cookie"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "scopes_exceed_issuer"

    # 5. Master role session trying to assign "all" -> success
    mock_oauth.validate_token.return_value = {
        "sub": "admin",
        "ocat_role": "master",
    }
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "My Key", "scopes": ["all"]},
        cookies={"session": "master_cookie"},
    )
    assert response.status_code == 200

    # 6. Master role session trying to assign "admin" -> success
    mock_oauth.validate_token.return_value = {
        "sub": "admin",
        "ocat_role": "master",
    }
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "My Key", "scopes": ["admin"]},
        cookies={"session": "master_cookie"},
    )
    assert response.status_code == 200

    # 7. Admin role session trying to assign "admin" -> success
    mock_oauth.validate_token.return_value = {
        "sub": "admin",
        "ocat_role": "admin",
    }
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "My Key", "scopes": ["admin"]},
        cookies={"session": "admin_cookie"},
    )
    assert response.status_code == 200

    # 8. Admin role session trying to assign "all" -> 403 Forbidden
    mock_oauth.validate_token.return_value = {
        "sub": "admin",
        "ocat_role": "admin",
    }
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "My Key", "scopes": ["all"]},
        cookies={"session": "admin_cookie"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "scopes_exceed_issuer"
    assert "all" in response.json()["excess"]


def test_login_api_key_stamps_master_role_for_admin_key(monkeypatch):
    """A key resolving to the admin sentinel mints a session with ocat_role=master."""
    async def mock_lookup(token):
        return {
            "key_id": "ak_admin",
            "subject": "admin",
            "expires_at": None,
            "scopes": ["all"],
            "tenant_id": 1,
        }

    monkeypatch.setattr("api.api_key_routes.lookup_active_by_token", mock_lookup)
    mock_oauth_service = MagicMock()
    mock_oauth_service.ensure_internal_client = AsyncMock()
    mock_oauth_service._issue_token_pair = AsyncMock(
        return_value={"access_token": "a", "refresh_token": "r"}
    )
    monkeypatch.setattr("api.api_key_routes._get_oauth_service", lambda: mock_oauth_service)

    client = TestClient(app)
    response = client.post("/api/admin/public/login-api-key", json={"api_key": "octk_admin"})
    assert response.status_code == 200
    call_kwargs = mock_oauth_service._issue_token_pair.await_args.kwargs
    assert call_kwargs["extra_claims"]["ocat_role"] == "master"


def test_login_api_key_stamps_lowest_role_for_narrow_key(monkeypatch):
    """Escalation regression: a narrow key must NOT get ocat_role=master.

    Previously admin_login_api_key stamped no ocat_role claim at all, so
    _resolve_issuer_role's old unconditional "master" fallback let this same
    narrow key log in and mint master-vocabulary keys.
    """
    async def mock_lookup(token):
        return {
            "key_id": "ak_narrow",
            "subject": "user-1",
            "expires_at": None,
            "scopes": ["plugin:fake_plugin"],
            "tenant_id": 2,
        }

    monkeypatch.setattr("api.api_key_routes.lookup_active_by_token", mock_lookup)
    mock_oauth_service = MagicMock()
    mock_oauth_service.ensure_internal_client = AsyncMock()
    mock_oauth_service._issue_token_pair = AsyncMock(
        return_value={"access_token": "a", "refresh_token": "r"}
    )
    monkeypatch.setattr("api.api_key_routes._get_oauth_service", lambda: mock_oauth_service)

    client = TestClient(app)
    response = client.post("/api/admin/public/login-api-key", json={"api_key": "octk_narrow"})
    assert response.status_code == 200
    call_kwargs = mock_oauth_service._issue_token_pair.await_args.kwargs
    assert call_kwargs["extra_claims"]["ocat_role"] != "master"
    from core.api_key_management.scopes import role_has_admin_bypass
    assert role_has_admin_bypass(call_kwargs["extra_claims"]["ocat_role"]) is False


def test_lowest_privilege_role_excludes_admin_bypass_roles():
    from api.api_key_routes import _lowest_privilege_role
    from core.api_key_management.scopes import role_has_admin_bypass

    role = _lowest_privilege_role()
    assert role_has_admin_bypass(role) is False


def test_admin_cannot_mint_all_even_if_role_config_lists_all(monkeypatch):
    """Non-master roles must not mint full-access sentinels via config lists."""
    import utils.server_config as sc

    monkeypatch.setitem(
        sc.ROLES_CONFIG,
        "admin",
        {
            "scopes": ["admin", "all", "*"],
            "include_data_plugins": True,
            "exclude_prefixes": ["terminal:"],
        },
    )

    async def mock_create(subject, name, expires_at, scopes=None, tenant_id=None):
        return {
            "key_id": "ak_should_not",
            "token": "octk_x",
            "prefix": "octk_x",
            "name": name,
            "expires_at": expires_at,
            "scopes": scopes,
        }

    monkeypatch.setattr("api.api_key_routes.create_api_key", mock_create)
    mock_oauth = MagicMock()
    mock_oauth.validate_token = AsyncMock(
        return_value={"sub": "admin", "ocat_role": "admin"}
    )
    monkeypatch.setattr("api.api_key_routes._get_oauth_service", lambda: mock_oauth)

    client = TestClient(app)
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "Bad All Key", "scopes": ["all"]},
        cookies={"session": "admin_cookie"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "scopes_exceed_issuer"


def test_narrow_session_grant_cannot_mint_role_floor_defaults(monkeypatch):
    """Regression: the issuer ceiling used to be scopes_for_role(role) alone.

    A session minted via admin_login_api_key for a key holding only
    core:apikey:read (role floor "operator", which lists core:graph:write
    among its defaults) must not be able to mint core:graph:write — it
    never actually held that scope. Requesting exactly what it holds still
    succeeds.
    """
    async def mock_create(subject, name, expires_at, scopes=None, tenant_id=None):
        return {
            "key_id": "ak_narrow",
            "token": "octk_narrow",
            "prefix": "octk_narrow",
            "name": name,
            "expires_at": expires_at,
            "scopes": scopes,
        }

    monkeypatch.setattr("api.api_key_routes.create_api_key", mock_create)
    mock_oauth = MagicMock()
    mock_oauth.validate_token = AsyncMock(
        return_value={
            "sub": "narrow-key-session",
            "ocat_role": "operator",
            "scopes": ["core:apikey:read"],
        }
    )
    monkeypatch.setattr("api.api_key_routes._get_oauth_service", lambda: mock_oauth)

    client = TestClient(app)

    # Role floor for "operator" includes core:graph:write, but the session's
    # real grant does not — must be denied.
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "Escalated Key", "scopes": ["core:graph:write"]},
        cookies={"session": "narrow_cookie"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "scopes_exceed_issuer"
    assert "core:graph:write" in response.json()["excess"]

    # Requesting exactly the held scope still succeeds.
    response = client.post(
        "/api/auth/session_gated/api-keys",
        json={"name": "Same Scope Key", "scopes": ["core:apikey:read"]},
        cookies={"session": "narrow_cookie"},
    )
    assert response.status_code == 200


def test_routes_require_tenant_id_fail_closed():
    client = TestClient(app)
    unauth_cookies = {"session": "missing_tenant"}

    # 1. GET api-keys -> 401 unauthenticated
    res = client.get("/api/auth/session_gated/api-keys", cookies=unauth_cookies)
    assert res.status_code == 401
    assert res.json()["error"] == "unauthenticated"

    # 2. POST api-keys -> 401 unauthenticated
    res = client.post("/api/auth/session_gated/api-keys", json={"name": "test"}, cookies=unauth_cookies)
    assert res.status_code == 401
    assert res.json()["error"] == "unauthenticated"

    # 3. POST api-keys/{id}/revoke -> 401 unauthenticated
    res = client.post("/api/auth/session_gated/api-keys/ak_1/revoke", cookies=unauth_cookies)
    assert res.status_code == 401
    assert res.json()["error"] == "unauthenticated"

    # 4. DELETE api-keys/{id} -> 401 unauthenticated
    res = client.delete("/api/auth/session_gated/api-keys/ak_1", cookies=unauth_cookies)
    assert res.status_code == 401
    assert res.json()["error"] == "unauthenticated"

    # 5. PUT api-keys/{id}/scopes -> 401 unauthenticated
    res = client.put("/api/auth/session_gated/api-keys/ak_1/scopes", json={"scopes": []}, cookies=unauth_cookies)
    assert res.status_code == 401
    assert res.json()["error"] == "unauthenticated"



