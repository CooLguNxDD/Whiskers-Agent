import pytest
from unittest.mock import patch, AsyncMock
import api.admin_routes as ar

class TestAdminExists:
    @pytest.mark.asyncio
    async def test_vault_none_returns_503(self, client):
        with patch("api.admin_routes.vault", None):
            res = await client.get("/api/admin/public/exists")
            assert res.status_code == 503

    @pytest.mark.asyncio
    async def test_no_admin_created_returns_false(self, client, mock_vault):
        mock_vault.get.side_effect = None
        mock_vault.get.return_value = None
        res = await client.get("/api/admin/public/exists")
        assert res.status_code == 200
        assert res.json() == {"exists": False}

    @pytest.mark.asyncio
    async def test_admin_exists_returns_true(self, client, mock_vault):
        mock_vault.get.side_effect = None
        mock_vault.get.return_value = "admin"
        res = await client.get("/api/admin/public/exists")
        assert res.status_code == 200
        assert res.json() == {"exists": True}

    @pytest.mark.asyncio
    async def test_vault_exception_returns_503(self, client, mock_vault):
        mock_vault.get.return_value = None
        mock_vault.get.side_effect = Exception("db down")
        res = await client.get("/api/admin/public/exists")
        assert res.status_code == 503

class TestAdminSignup:
    @pytest.mark.asyncio
    async def test_missing_username_returns_400(self, client):
        res = await client.post("/api/admin/public/signup", json={"password": "p"})
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_password_returns_400(self, client):
        res = await client.post("/api/admin/public/signup", json={"username": "u"})
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, client):
        res = await client.post("/api/admin/public/signup", content="not json")
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_non_dict_json_returns_400(self, client):
        res = await client.post("/api/admin/public/signup", json=[1, 2, 3])
        assert res.status_code == 400
        assert res.json() == {"error": "invalid_json", "message": "Expected a JSON object"}

    @pytest.mark.asyncio
    async def test_already_exists_returns_409(self, client, mock_vault):
        mock_vault.get.side_effect = None
        mock_vault.get.return_value = "admin"
        res = await client.post("/api/admin/public/signup", json={"username": "u", "password": "p"})
        assert res.status_code == 409

    @pytest.mark.asyncio
    async def test_oauth_unavailable_returns_503(self, client, mock_vault):
        mock_vault.get.side_effect = None
        mock_vault.get.return_value = None
        with patch("api.admin_routes.oauth_provider", None):
            res = await client.post("/api/admin/public/signup", json={"username": "u", "password": "p"})
            assert res.status_code == 503

    @pytest.mark.asyncio
    async def test_successful_signup_sets_cookies(self, client, mock_vault, mock_oauth_provider):
        mock_vault.get.side_effect = None
        mock_vault.get.return_value = None
        with patch("core.user_management.store.hash_password", return_value="$argon2id$v=19$hash"), \
             patch("api.admin_routes._ensure_admin_plugin_row", new_callable=AsyncMock):
            res = await client.post("/api/admin/public/signup", json={"username": "u", "password": "p"})

            assert res.status_code == 200
            assert "session" in res.cookies
            assert "refresh" in res.cookies
            extra = mock_oauth_provider._svc._issue_token_pair.call_args.kwargs.get("extra_claims")
            assert extra == {"ocat_role": "master", "ocat_tenant": 1}

class TestAdminLogin:
    @pytest.mark.asyncio
    async def test_rate_limit_after_5_attempts(self, client):
        import time
        now = time.monotonic()
        ar._login_attempts["127.0.0.1"] = [now] * 5
        res = await client.post("/api/admin/public/login", json={"username": "u", "password": "p"}, headers={"X-Forwarded-For": "127.0.0.1"})
        assert res.status_code == 429
        ar._login_attempts.clear()

    @pytest.mark.asyncio
    async def test_missing_credentials_returns_400(self, client):
        res = await client.post("/api/admin/public/login", json={"username": "u"})
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_non_dict_json_returns_400(self, client):
        res = await client.post("/api/admin/public/login", json="invalid")
        assert res.status_code == 400
        assert res.json() == {"error": "invalid_json", "message": "Expected a JSON object"}

    @pytest.mark.asyncio
    async def test_admin_not_configured_returns_503(self, client, mock_vault):
        mock_vault.get.side_effect = None
        mock_vault.get.return_value = None
        res = await client.post("/api/admin/public/login", json={"username": "u", "password": "p"})
        assert res.status_code == 503

    @pytest.mark.asyncio
    async def test_wrong_password_returns_401(self, client, mock_vault):
        def vault_side_effect(ns, key):
            if key == "username": return "u"
            if key == "password_hash": return "hashed"
            return None
        mock_vault.get.side_effect = vault_side_effect
        with patch("bcrypt.checkpw", return_value=False):
            res = await client.post("/api/admin/public/login", json={"username": "u", "password": "wrong"})
            assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_successful_login_sets_cookies(self, client, mock_vault):
        def vault_side_effect(ns, key):
            if key == "username": return "u"
            if key == "password_hash": return "hashed"
            return None
            
        mock_vault.get.side_effect = vault_side_effect
        with patch("bcrypt.checkpw", return_value=True):
            res = await client.post("/api/admin/public/login", json={"username": "u", "password": "p"})
            assert res.status_code == 200
            assert "session" in res.cookies
            assert "refresh" in res.cookies

    @pytest.mark.asyncio
    async def test_next_url_safe_redirect(self, client, mock_vault):
        def vault_side_effect(ns, key):
            if key == "username": return "u"
            if key == "password_hash": return "hashed"
            return None
        mock_vault.get.side_effect = vault_side_effect
        with patch("bcrypt.checkpw", return_value=True):
            res = await client.post("/api/admin/public/login", json={"username": "u", "password": "p", "next": "/dashboard"})
            assert res.status_code == 200
            assert res.json() == {"redirect": "/dashboard"}

    @pytest.mark.asyncio
    async def test_next_url_external_blocked(self, client, mock_vault):
        def vault_side_effect(ns, key):
            if key == "username": return "u"
            if key == "password_hash": return "hashed"
            return None
        mock_vault.get.side_effect = vault_side_effect
        with patch("bcrypt.checkpw", return_value=True):
            res = await client.post("/api/admin/public/login", json={"username": "u", "password": "p", "next": "http://evil.com"})
            assert res.status_code == 200
            assert res.json() == {"redirect": "/"}


class TestAdminLogout:
    @pytest.mark.asyncio
    async def test_logout_always_clears_cookies(self, client):
        res = await client.post("/api/admin/public/logout")
        assert res.status_code == 200
        # When cookie is cleared by httpx, it deletes it from jar or sets it to empty
        assert "session" not in res.cookies or res.cookies["session"] == ""

    @pytest.mark.asyncio
    async def test_logout_calls_revoke_for_valid_token(self, client, mock_oauth_provider):
        res = await client.post("/api/admin/public/logout", cookies={"session": "test_tok"})
        assert res.status_code == 200
        mock_oauth_provider._svc.revoke_token.assert_called_with("test-jti")

class TestAdminRefresh:
    @pytest.mark.asyncio
    async def test_no_refresh_cookie_returns_401(self, client):
        res = await client.post("/api/admin/public/refresh")
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401_and_clears_cookies(self, client, mock_oauth_provider):
        from oauth.oauth_service import InvalidGrantError
        mock_oauth_provider._svc.refresh_grant.side_effect = InvalidGrantError("bad")
        res = await client.post("/api/admin/public/refresh", cookies={"refresh": "bad"})
        assert res.status_code == 401
        assert "session" not in res.cookies or res.cookies["session"] == ""

    @pytest.mark.asyncio
    async def test_successful_refresh_rotates_cookies(self, client, mock_oauth_provider):
        mock_oauth_provider._svc.refresh_grant.side_effect = None
        res = await client.post("/api/admin/public/refresh", cookies={"refresh": "good"})
        assert res.status_code == 200
        assert res.cookies.get("session") == "new.acc"
        assert res.cookies.get("refresh") == "new.ref"

class TestAdminMe:
    @pytest.mark.asyncio
    async def test_no_cookie_returns_401(self, test_app):
        import httpx
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon_c:
            res = await anon_c.get("/api/admin/public/me")
            assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self, client, mock_oauth_provider):
        mock_oauth_provider._svc.validate_token.side_effect = Exception("bad")
        res = await client.get("/api/admin/public/me", cookies={"session": "bad"})
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_returns_payload(self, client, mock_oauth_provider):
        mock_oauth_provider._svc.validate_token.side_effect = None
        res = await client.get("/api/admin/public/me", cookies={"session": "good"})
        assert res.status_code == 200
        body = res.json()
        assert body["subject"] == "admin"
        assert body["iat"] == 1700000000
