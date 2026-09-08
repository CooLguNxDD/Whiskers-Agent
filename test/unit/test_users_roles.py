"""Unit tests for the User model schema, password utilities, and default admin seeding."""

import pytest
import bcrypt
from unittest.mock import patch
from sqlalchemy import select, delete

from db_layer.connection import get_async_session
from db_layer.models import User
from core.user_management import (
    ensure_default_admin_user,
    get_user_by_username,
    verify_password,
    hash_password,
)


@pytest.fixture(autouse=True)
async def cleanup_users():
    """Autouse fixture to clean up the users table before and after each test."""
    async with get_async_session() as session:
        await session.execute(delete(User))
        await session.commit()
    yield
    async with get_async_session() as session:
        await session.execute(delete(User))
        await session.commit()


def test_user_model_schema():
    """Verify that User model has correct columns, table name, and defaults."""
    from db_layer.models import User
    assert User.__tablename__ == "users"

    expected_columns = [
        "id",
        "username",
        "password_hash",
        "role",
        "tenant_id",
        "is_active",
        "created_at",
    ]
    actual_columns = User.__table__.columns.keys()
    assert len(actual_columns) == len(expected_columns)
    for col in expected_columns:
        assert col in actual_columns


def test_user_migration():
    """Verify core_033 migration configuration and revision chain."""
    import migrations.versions.core.core_033_users as migration
    assert migration.revision == "core_033"
    assert migration.down_revision == "core_032"
    assert migration.branch_labels is None


def test_password_hashing_and_verification():
    """Test Argon2 hashing/verification roundtrip and Bcrypt fallback verification."""
    # Argon2 hashing and verify roundtrip
    password = "secure_password_123"
    hashed = hash_password(password)
    assert hashed.startswith("$argon2")
    assert verify_password(hashed, password) is True
    assert verify_password(hashed, "wrong_password") is False
    assert verify_password(hashed, "") is False

    # Bcrypt verification path
    bcrypt_password = "legacy_password"
    bcrypt_hashed = bcrypt.hashpw(bcrypt_password.encode("utf-8"), bcrypt.gensalt(12)).decode("utf-8")
    assert verify_password(bcrypt_hashed, bcrypt_password) is True
    assert verify_password(bcrypt_hashed, "wrong_password") is False

    # Edge cases/invalid inputs
    assert verify_password("", password) is False
    assert verify_password(hashed, None) is False
    assert verify_password(None, password) is False


@pytest.mark.asyncio
async def test_ensure_default_admin_user_idempotency():
    """Verify ensure_default_admin_user creates the admin and is idempotent."""
    bcrypt_hash = bcrypt.hashpw(b"admin_password", bcrypt.gensalt(12)).decode("utf-8")

    with patch("db_layer.vault.VaultService.get") as mock_get:
        def get_side_effect(subject, key):
            if subject == "admin" and key == "username":
                return "admin"
            if subject == "admin" and key == "password_hash":
                return bcrypt_hash
            return None
        mock_get.side_effect = get_side_effect

        # First execution should create the admin user
        await ensure_default_admin_user()
        assert mock_get.call_count == 2
        mock_get.assert_any_call("admin", "username")
        mock_get.assert_any_call("admin", "password_hash")

        # Verify the user was created correctly
        async with get_async_session() as session:
            stmt = select(User).where(User.username == "admin")
            result = await session.execute(stmt)
            users = result.scalars().all()
            assert len(users) == 1
            assert users[0].role == "master"
            assert users[0].password_hash == bcrypt_hash

        # Second execution should do nothing since a master role user exists
        mock_get.reset_mock()
        await ensure_default_admin_user()
        mock_get.assert_not_called()

        # Check that we still have exactly one master user
        async with get_async_session() as session:
            stmt = select(User).where(User.role == "master")
            result = await session.execute(stmt)
            users = result.scalars().all()
            assert len(users) == 1


@pytest.mark.asyncio
async def test_ensure_default_admin_user_vault_empty():
    """Verify ensure_default_admin_user handles empty vault gracefully."""
    with patch("db_layer.vault.VaultService.get", return_value=None) as mock_get:
        await ensure_default_admin_user()
        assert mock_get.call_count == 2
        mock_get.assert_any_call("admin", "username")
        mock_get.assert_any_call("admin", "password_hash")

        # Verify no user was created
        async with get_async_session() as session:
            stmt = select(User)
            result = await session.execute(stmt)
            users = result.scalars().all()
            assert len(users) == 0


@pytest.mark.asyncio
async def test_get_user_by_username():
    """Test retrieving user details via get_user_by_username."""
    async with get_async_session() as session:
        user = User(username="operator1", password_hash="dummy_hash", role="operator")
        session.add(user)
        await session.commit()

    retrieved = await get_user_by_username("operator1")
    assert retrieved is not None
    assert retrieved["username"] == "operator1"
    assert retrieved["role"] == "operator"
    assert retrieved["password_hash"] == "dummy_hash"

    not_found = await get_user_by_username("nonexistent")
    assert not_found is None


def test_scopes_for_role(monkeypatch):
    """Test scopes_for_role resolution against OAUTH_VALID_SCOPES and ROLES_CONFIG."""
    import utils.server_config
    from core.api_key_management.scopes import scopes_for_role

    # Ensure OAUTH_VALID_SCOPES has the expected scopes
    vocab = [
        "whiskers",
        "terminal:use",
        "terminal:host",
        "admin",
        "plugin:jules_plugin",
        "group:jules_plugin:sessions",
        "group:jules_plugin:read",
        "group:jules_plugin:write_update",
    ]
    monkeypatch.setattr(utils.server_config, "OAUTH_VALID_SCOPES", vocab)

    # 1. master -> full vocab
    master_scopes = scopes_for_role("master")
    assert "admin" in master_scopes
    assert "plugin:jules_plugin" in master_scopes
    non_core_master_scopes = [s for s in master_scopes if not s.startswith("core:")]
    assert len(non_core_master_scopes) == len(vocab)

    # 2. admin -> contains admin
    admin_scopes = scopes_for_role("admin")
    assert "admin" in admin_scopes
    assert "core:terminal:write" not in admin_scopes

    # 3. operator -> contains core:whiskers.console:write, core:config:read, plugin tokens, NOT admin, NOT terminal:*
    operator_scopes = scopes_for_role("operator")
    assert "plugin:jules_plugin" in operator_scopes
    assert "group:jules_plugin:read" in operator_scopes
    assert "core:config:read" in operator_scopes
    assert "core:graph:write" in operator_scopes
    assert "core:graph:read" in operator_scopes  # implied by write
    assert "admin" not in operator_scopes
    assert "core:terminal:write" not in operator_scopes
    assert "core:terminal:read" not in operator_scopes

    # 4. viewer -> contains read core tokens, read plugin tokens, NOT write, NOT terminal:*/admin
    viewer_scopes = scopes_for_role("viewer")
    assert "group:jules_plugin:read" in viewer_scopes
    assert "group:jules_plugin:write_update" not in viewer_scopes
    assert "core:graph:read" in viewer_scopes
    assert "core:graph:write" not in viewer_scopes
    assert "admin" not in viewer_scopes
    assert "core:terminal:write" not in viewer_scopes
    assert "core:terminal:read" not in viewer_scopes

    # 5. unknown role -> []
    unknown_scopes = scopes_for_role("unknown_role")
    assert unknown_scopes == []


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    import api.admin_routes as ar
    ar._login_attempts.clear()
    ar._last_rate_limit_prune = 0.0
    yield
    ar._login_attempts.clear()


@pytest.mark.asyncio
async def test_oauth_service_extra_claims():
    from oauth.oauth_service import OAuthService
    service = OAuthService()
    await service.ensure_internal_client("test-client", scopes="admin")
    pair = await service._issue_token_pair(
        client_id="test-client",
        scopes=["admin"],
        extra_claims={"ocat_role": "viewer", "ocat_user_id": "user_123"}
    )
    assert pair["access_token"] is not None
    
    payload = await service.validate_token(pair["access_token"])
    assert payload["ocat_role"] == "viewer"
    assert payload["ocat_user_id"] == "user_123"


@pytest.mark.asyncio
async def test_admin_login_success(monkeypatch):
    from unittest.mock import MagicMock, AsyncMock
    from api.admin_routes import admin_login
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.routing import Route
    
    # Let's seed an operator user in the DB
    from core.user_management import hash_password
    async with get_async_session() as session:
        user = User(
            username="operator_tom",
            password_hash=hash_password("tom_pass_123"),
            role="operator",
            is_active=True
        )
        session.add(user)
        await session.commit()
        # Retrieve to get the ID
        user_dict = await get_user_by_username("operator_tom")
        user_id = user_dict["id"]

    mock_oauth_service = MagicMock()
    mock_oauth_service.ensure_internal_client = AsyncMock()
    mock_oauth_service._issue_token_pair = AsyncMock(
        return_value={"access_token": "token_acc", "refresh_token": "token_ref"}
    )
    monkeypatch.setattr("api.admin_routes._get_oauth_service", lambda: mock_oauth_service)
    
    app = Starlette(routes=[Route("/api/admin/public/login", admin_login, methods=["POST"])])
    client = TestClient(app)
    
    response = client.post("/api/admin/public/login", json={"username": "operator_tom", "password": "tom_pass_123"})
    assert response.status_code == 200
    assert response.json()["redirect"] == "/"
    assert response.cookies["session"] == "token_acc"
    
    # Verify mock_oauth_service call parameters
    mock_oauth_service._issue_token_pair.assert_called_once()
    args, kwargs = mock_oauth_service._issue_token_pair.call_args
    # scopes list should be from role "operator"
    from core.api_key_management.scopes import scopes_for_role
    expected_scopes = scopes_for_role("operator")
    assert kwargs["scopes"] == expected_scopes
    assert kwargs["extra_claims"] == {
        "ocat_role": "operator",
        "ocat_user_id": str(user_id),
        "ocat_tenant": 1,
        "whiskers_role": "operator",
        "whiskers_user_id": str(user_id),
        "whiskers_tenant": 1,
    }


@pytest.mark.asyncio
async def test_admin_login_master_success(monkeypatch):
    from unittest.mock import MagicMock, AsyncMock
    from api.admin_routes import admin_login
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.routing import Route
    
    from core.user_management import hash_password
    async with get_async_session() as session:
        user = User(
            username="master_user",
            password_hash=hash_password("master_pass_123"),
            role="master",
            is_active=True
        )
        session.add(user)
        await session.commit()
        user_dict = await get_user_by_username("master_user")
        user_id = user_dict["id"]

    mock_oauth_service = MagicMock()
    mock_oauth_service.ensure_internal_client = AsyncMock()
    mock_oauth_service._issue_token_pair = AsyncMock(
        return_value={"access_token": "token_acc", "refresh_token": "token_ref"}
    )
    monkeypatch.setattr("api.admin_routes._get_oauth_service", lambda: mock_oauth_service)
    
    app = Starlette(routes=[Route("/api/admin/public/login", admin_login, methods=["POST"])])
    client = TestClient(app)
    
    response = client.post("/api/admin/public/login", json={"username": "master_user", "password": "master_pass_123"})
    assert response.status_code == 200
    
    mock_oauth_service._issue_token_pair.assert_called_once()
    args, kwargs = mock_oauth_service._issue_token_pair.call_args
    assert kwargs["scopes"] == ["admin"]
    assert kwargs["extra_claims"] == {
        "ocat_role": "master",
        "ocat_user_id": str(user_id),
        "ocat_tenant": 1,
        "whiskers_role": "master",
        "whiskers_user_id": str(user_id),
        "whiskers_tenant": 1,
    }


@pytest.mark.asyncio
async def test_admin_login_wrong_password(monkeypatch):
    from unittest.mock import MagicMock
    from api.admin_routes import admin_login
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.routing import Route
    
    from core.user_management import hash_password
    async with get_async_session() as session:
        user = User(
            username="operator_tom",
            password_hash=hash_password("tom_pass_123"),
            role="operator",
            is_active=True
        )
        session.add(user)
        await session.commit()

    mock_oauth_service = MagicMock()
    monkeypatch.setattr("api.admin_routes._get_oauth_service", lambda: mock_oauth_service)
    
    app = Starlette(routes=[Route("/api/admin/public/login", admin_login, methods=["POST"])])
    client = TestClient(app)
    
    response = client.post("/api/admin/public/login", json={"username": "operator_tom", "password": "wrong_password"})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_admin_login_inactive_user(monkeypatch):
    from unittest.mock import MagicMock
    from api.admin_routes import admin_login
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.routing import Route
    
    from core.user_management import hash_password
    async with get_async_session() as session:
        user = User(
            username="operator_tom",
            password_hash=hash_password("tom_pass_123"),
            role="operator",
            is_active=False
        )
        session.add(user)
        await session.commit()

    mock_oauth_service = MagicMock()
    monkeypatch.setattr("api.admin_routes._get_oauth_service", lambda: mock_oauth_service)
    
    app = Starlette(routes=[Route("/api/admin/public/login", admin_login, methods=["POST"])])
    client = TestClient(app)
    
    response = client.post("/api/admin/public/login", json={"username": "operator_tom", "password": "tom_pass_123"})
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_admin_login_vault_fallback(monkeypatch):
    from unittest.mock import MagicMock, AsyncMock
    from api.admin_routes import admin_login
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.routing import Route
    from core.user_management import hash_password
    
    # Ensure users table is empty
    async with get_async_session() as session:
        await session.execute(delete(User))
        await session.commit()

    mock_oauth_service = MagicMock()
    mock_oauth_service.ensure_internal_client = AsyncMock()
    mock_oauth_service._issue_token_pair = AsyncMock(
        return_value={"access_token": "token_acc", "refresh_token": "token_ref"}
    )
    monkeypatch.setattr("api.admin_routes._get_oauth_service", lambda: mock_oauth_service)
    
    # Mock VaultService
    import bcrypt
    bcrypt_hash = bcrypt.hashpw(b"vault_pass_123", bcrypt.gensalt(12)).decode("utf-8")
    with patch("db_layer.vault.VaultService.get") as mock_vault_get:
        def get_side_effect(subject, key):
            if subject == "admin" and key == "username":
                return "admin_vault"
            if subject == "admin" and key == "password_hash":
                return bcrypt_hash
            return None
        mock_vault_get.side_effect = get_side_effect
        
        app = Starlette(routes=[Route("/api/admin/public/login", admin_login, methods=["POST"])])
        client = TestClient(app)
        
        response = client.post("/api/admin/public/login", json={"username": "admin_vault", "password": "vault_pass_123"})
        assert response.status_code == 200
        assert response.json()["redirect"] == "/"
        
        mock_oauth_service._issue_token_pair.assert_called_once()
        args, kwargs = mock_oauth_service._issue_token_pair.call_args
        assert kwargs["scopes"] == ["admin"]
        assert kwargs["extra_claims"] == {
            "ocat_role": "master",
            "ocat_tenant": 1,
            "whiskers_role": "master",
            "whiskers_tenant": 1,
        }


@pytest.mark.asyncio
async def test_resolve_subject():
    from api.api_key_routes import _resolve_subject
    from unittest.mock import MagicMock, AsyncMock, patch
    from starlette.requests import Request

    # Helper to construct a mock request with a session cookie
    def make_mock_request(session_cookie):
        req = MagicMock(spec=Request)
        req.cookies = {"session": session_cookie} if session_cookie else {}
        return req

    # Mock the OAuth service validation return value
    mock_svc = MagicMock()
    mock_svc.validate_token = AsyncMock()

    # We will monkeypatch _get_oauth_service
    with patch("api.api_key_routes._get_oauth_service", return_value=mock_svc):
        # Case 1: no session cookie -> returns "admin"
        req = make_mock_request(None)
        res = await _resolve_subject(req)
        assert res == "admin"

        # Case 2: ocat_user_id claim present -> returns str(ocat_user_id)
        mock_svc.validate_token.reset_mock()
        mock_svc.validate_token.return_value = {
            "sub": "admin-console",
            "ocat_user_id": 42
        }
        req = make_mock_request("valid_session")
        res = await _resolve_subject(req)
        assert res == "42"
        mock_svc.validate_token.assert_called_once_with("valid_session")

        # Case 3: username claim present, ocat_user_id missing -> returns username
        mock_svc.validate_token.reset_mock()
        mock_svc.validate_token.return_value = {
            "sub": "admin-console",
            "username": "operator_bob"
        }
        req = make_mock_request("valid_session")
        res = await _resolve_subject(req)
        assert res == "operator_bob"

        # Case 4: sub is "admin-console", no user claims -> returns "admin" (legacy fallback)
        mock_svc.validate_token.reset_mock()
        mock_svc.validate_token.return_value = {
            "sub": "admin-console"
        }
        req = make_mock_request("valid_session")
        res = await _resolve_subject(req)
        assert res == "admin"

        # Case 5: sub is other than "admin-console", no user claims -> returns sub
        mock_svc.validate_token.reset_mock()
        mock_svc.validate_token.return_value = {
            "sub": "some-other-sub"
        }
        req = make_mock_request("valid_session")
        res = await _resolve_subject(req)
        assert res == "some-other-sub"

        # Case 6: validate_token raises error -> returns "admin"
        mock_svc.validate_token.reset_mock()
        mock_svc.validate_token.side_effect = Exception("invalid token")
        req = make_mock_request("invalid_session")
        res = await _resolve_subject(req)
        assert res == "admin"



