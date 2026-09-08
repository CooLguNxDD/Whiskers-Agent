"""Integration tests for upstream MCP proxy REST control plane routes."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import httpx


@pytest_asyncio.fixture
async def proxy_client(test_app, mock_oauth_provider):
    """Fixture supplying an ASGI test client with authenticated OAuth relay mock."""
    from unittest.mock import patch
    import api.proxy_routes
    
    # Patch oauth_provider on the proxy_routes module so auth cookie validation works
    with patch("api.proxy_routes.oauth_provider", mock_oauth_provider):
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


class TestListProxies:
    @pytest.mark.asyncio
    async def test_list_proxies_unauthenticated(self, proxy_client):
        res = await proxy_client.get("/api/proxies/session_gated")
        assert res.status_code == 401
        assert res.json() == {"error": "unauthenticated"}

    @pytest.mark.asyncio
    async def test_list_proxies_success(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.list_proxies", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = [{"name": "proxy1", "transport": "http", "url": "http://upstream"}]
            
            res = await proxy_client.get("/api/proxies/session_gated", cookies={"session": "valid_token"})
            assert res.status_code == 200
            assert res.json() == [{"name": "proxy1", "transport": "http", "url": "http://upstream"}]
            mock_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_proxies_internal_error(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.list_proxies", new_callable=AsyncMock) as mock_list:
            mock_list.side_effect = Exception("DB Down")
            
            res = await proxy_client.get("/api/proxies/session_gated", cookies={"session": "valid_token"})
            assert res.status_code == 500
            assert res.json()["error"] == "internal_error"
            assert res.json()["message"] == "An unexpected error occurred."


class TestAddProxy:
    @pytest.mark.asyncio
    async def test_add_proxy_unauthenticated(self, proxy_client):
        res = await proxy_client.post("/api/proxies/session_gated", json={"name": "test"})
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_add_proxy_invalid_json(self, proxy_client):
        res = await proxy_client.post(
            "/api/proxies/session_gated",
            content="not-json",
            headers={"Content-Type": "application/json"},
            cookies={"session": "valid_token"}
        )
        assert res.status_code == 400
        assert res.json() == {"error": "invalid_json"}

    @pytest.mark.asyncio
    async def test_add_proxy_non_dict_json(self, proxy_client):
        res = await proxy_client.post(
            "/api/proxies/session_gated",
            json=[1, 2, 3],
            cookies={"session": "valid_token"}
        )
        assert res.status_code == 400
        assert res.json() == {"error": "invalid_json", "message": "Expected a JSON object"}

    @pytest.mark.asyncio
    async def test_add_proxy_missing_fields(self, proxy_client):
        res = await proxy_client.post(
            "/api/proxies/session_gated",
            json={"name": "test-proxy"},
            cookies={"session": "valid_token"}
        )
        assert res.status_code == 400
        assert res.json()["error"] == "missing_required_fields"

    @pytest.mark.asyncio
    async def test_add_proxy_invalid_name(self, proxy_client):
        res = await proxy_client.post(
            "/api/proxies/session_gated",
            json={"name": "invalid name!", "transport": "http", "url": "http://upstream"},
            cookies={"session": "valid_token"}
        )
        assert res.status_code == 400
        assert res.json()["error"] == "invalid_name"

    @pytest.mark.asyncio
    async def test_add_proxy_invalid_transport(self, proxy_client):
        res = await proxy_client.post(
            "/api/proxies/session_gated",
            json={"name": "valid-name", "transport": "tcp", "url": "http://upstream"},
            cookies={"session": "valid_token"}
        )
        assert res.status_code == 400
        assert res.json()["error"] == "invalid_transport"

    @pytest.mark.asyncio
    async def test_add_proxy_invalid_url(self, proxy_client):
        res = await proxy_client.post(
            "/api/proxies/session_gated",
            json={"name": "valid-name", "transport": "http", "url": "ftp://upstream"},
            cookies={"session": "valid_token"}
        )
        assert res.status_code == 400
        assert res.json()["error"] == "invalid_url"

    @pytest.mark.asyncio
    async def test_add_proxy_https_required_in_production(self, proxy_client):
        with patch("core.context.MCP_SERVER_URL", "https://prod-server.com"):
            res = await proxy_client.post(
                "/api/proxies/session_gated",
                json={"name": "valid-name", "transport": "http", "url": "http://upstream"},
                cookies={"session": "valid_token"}
            )
            assert res.status_code == 400
            assert res.json()["error"] == "https_required"

    @pytest.mark.asyncio
    async def test_add_proxy_validation_error_from_manager(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.add_proxy", new_callable=AsyncMock) as mock_add:
            mock_add.side_effect = ValueError("Proxy name duplicate")
            
            res = await proxy_client.post(
                "/api/proxies/session_gated",
                json={"name": "valid-name", "transport": "http", "url": "http://upstream"},
                cookies={"session": "valid_token"}
            )
            assert res.status_code == 400
            assert res.json() == {"error": "validation_error", "message": "Proxy name duplicate"}

    @pytest.mark.asyncio
    async def test_add_proxy_internal_error(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.add_proxy", new_callable=AsyncMock) as mock_add:
            mock_add.side_effect = Exception("System Crash")
            
            res = await proxy_client.post(
                "/api/proxies/session_gated",
                json={"name": "valid-name", "transport": "http", "url": "http://upstream"},
                cookies={"session": "valid_token"}
            )
            assert res.status_code == 500
            assert res.json()["error"] == "internal_error"

    @pytest.mark.asyncio
    async def test_add_proxy_success(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.add_proxy", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = {"id": "uuid1", "name": "valid-name", "status": "active"}
            
            res = await proxy_client.post(
                "/api/proxies/session_gated",
                json={"name": "valid-name", "transport": "http", "url": "http://upstream", "bearerToken": "my-secret"},
                cookies={"session": "valid_token"}
            )
            assert res.status_code == 200
            assert res.json() == {"id": "uuid1", "name": "valid-name", "status": "active"}
            mock_add.assert_called_once_with(
                name="valid-name",
                transport="http",
                url="http://upstream",
                auth_mode="none",
                bearer_token="my-secret",
                oauth_config=None,
                client_secret=None,
                custom_description=None,
                workspace_label=None,
            )

    @pytest.mark.asyncio
    async def test_add_proxy_description_too_long(self, proxy_client):
        with patch("api.proxy_routes.MAX_PROXY_CUSTOM_DESCRIPTION_CHARS", 10):
            res = await proxy_client.post(
                "/api/proxies/session_gated",
                json={"name": "valid-name", "transport": "http", "url": "http://upstream", "customDescription": "123456789012345"},
                cookies={"session": "valid_token"}
            )
            assert res.status_code == 400
            assert res.json() == {"error": "invalid_description", "message": "customDescription too long"}


class TestRemoveProxy:
    @pytest.mark.asyncio
    async def test_remove_proxy_unauthenticated(self, proxy_client):
        res = await proxy_client.delete("/api/proxies/session_gated/my-proxy")
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_remove_proxy_not_found(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.remove_proxy", new_callable=AsyncMock) as mock_remove:
            mock_remove.side_effect = ValueError("Proxy not found")
            
            res = await proxy_client.delete("/api/proxies/session_gated/my-proxy", cookies={"session": "valid_token"})
            assert res.status_code == 404
            assert res.json() == {"error": "not_found", "message": "Proxy not found"}

    @pytest.mark.asyncio
    async def test_remove_proxy_internal_error(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.remove_proxy", new_callable=AsyncMock) as mock_remove:
            mock_remove.side_effect = Exception("Remove failure")
            
            res = await proxy_client.delete("/api/proxies/session_gated/my-proxy", cookies={"session": "valid_token"})
            assert res.status_code == 500
            assert res.json()["error"] == "internal_error"

    @pytest.mark.asyncio
    async def test_remove_proxy_success(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.remove_proxy", new_callable=AsyncMock) as mock_remove:
            mock_remove.return_value = {"ok": True, "restartRequired": False}
            
            res = await proxy_client.delete("/api/proxies/session_gated/my-proxy", cookies={"session": "valid_token"})
            assert res.status_code == 200
            assert res.json() == {"ok": True, "restartRequired": False}
            mock_remove.assert_called_once_with("my-proxy")


class TestTestProxy:
    @pytest.mark.asyncio
    async def test_test_proxy_unauthenticated(self, proxy_client):
        res = await proxy_client.post("/api/proxies/session_gated/my-proxy/test")
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_test_proxy_not_found(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.test_proxy", new_callable=AsyncMock) as mock_test:
            mock_test.side_effect = ValueError("Proxy not found")
            
            res = await proxy_client.post("/api/proxies/session_gated/my-proxy/test", cookies={"session": "valid_token"})
            assert res.status_code == 404
            assert res.json() == {"error": "not_found", "message": "Proxy not found"}

    @pytest.mark.asyncio
    async def test_test_proxy_internal_error(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.test_proxy", new_callable=AsyncMock) as mock_test:
            mock_test.side_effect = Exception("Test failure")
            
            res = await proxy_client.post("/api/proxies/session_gated/my-proxy/test", cookies={"session": "valid_token"})
            assert res.status_code == 500
            assert res.json()["error"] == "internal_error"

    @pytest.mark.asyncio
    async def test_test_proxy_success(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.test_proxy", new_callable=AsyncMock) as mock_test:
            mock_test.return_value = {"name": "my-proxy", "status": "active", "toolCount": 5}
            
            res = await proxy_client.post("/api/proxies/session_gated/my-proxy/test", cookies={"session": "valid_token"})
            assert res.status_code == 200
            assert res.json() == {"name": "my-proxy", "status": "active", "toolCount": 5}
            mock_test.assert_called_once_with("my-proxy")


class TestUpdateProxy:
    @pytest.mark.asyncio
    async def test_update_proxy_unauthenticated(self, proxy_client):
        res = await proxy_client.patch("/api/proxies/session_gated/my-proxy", json={"customDescription": "New Description"})
        assert res.status_code == 401

    @pytest.mark.asyncio
    async def test_update_proxy_invalid_json(self, proxy_client):
        res = await proxy_client.patch(
            "/api/proxies/session_gated/my-proxy",
            content="not-json",
            headers={"Content-Type": "application/json"},
            cookies={"session": "valid_token"}
        )
        assert res.status_code == 400
        assert res.json() == {"error": "invalid_json"}

    @pytest.mark.asyncio
    async def test_update_proxy_non_dict_json(self, proxy_client):
        res = await proxy_client.patch(
            "/api/proxies/session_gated/my-proxy",
            json=[1, 2, 3],
            cookies={"session": "valid_token"}
        )
        assert res.status_code == 400
        assert res.json() == {"error": "invalid_json", "message": "Expected a JSON object"}

    @pytest.mark.asyncio
    async def test_update_proxy_not_found(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.update_proxy_description", new_callable=AsyncMock) as mock_update:
            mock_update.side_effect = ValueError("Proxy 'my-proxy' not found.")
            
            res = await proxy_client.patch(
                "/api/proxies/session_gated/my-proxy",
                json={"customDescription": "New Description"},
                cookies={"session": "valid_token"}
            )
            assert res.status_code == 400
            assert res.json() == {"error": "validation_error", "message": "Proxy 'my-proxy' not found."}

    @pytest.mark.asyncio
    async def test_update_proxy_internal_error(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.update_proxy_description", new_callable=AsyncMock) as mock_update:
            mock_update.side_effect = Exception("DB connection lost")
            
            res = await proxy_client.patch(
                "/api/proxies/session_gated/my-proxy",
                json={"customDescription": "New Description"},
                cookies={"session": "valid_token"}
            )
            assert res.status_code == 500
            assert res.json()["error"] == "internal_error"

    @pytest.mark.asyncio
    async def test_update_proxy_success(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.update_proxy_description", new_callable=AsyncMock) as mock_update:
            mock_update.return_value = {
                "name": "my-proxy",
                "customDescription": "New Description",
                "status": "ok"
            }
            
            res = await proxy_client.patch(
                "/api/proxies/session_gated/my-proxy",
                json={"customDescription": "New Description"},
                cookies={"session": "valid_token"}
            )
            assert res.status_code == 200
            assert res.json() == {
                "name": "my-proxy",
                "customDescription": "New Description",
                "status": "ok"
            }
            mock_update.assert_called_once_with("my-proxy", custom_description="New Description")

    @pytest.mark.asyncio
    async def test_update_proxy_workspace_label_success(self, proxy_client):
        with patch("api.proxy_routes.proxy_manager.update_proxy_description", new_callable=AsyncMock) as mock_update:
            mock_update.return_value = {
                "name": "my-proxy",
                "workspaceLabel": "label-1",
                "status": "ok"
            }
            
            res = await proxy_client.patch(
                "/api/proxies/session_gated/my-proxy",
                json={"workspaceLabel": "label-1"},
                cookies={"session": "valid_token"}
            )
            assert res.status_code == 200
            assert res.json() == {
                "name": "my-proxy",
                "workspaceLabel": "label-1",
                "status": "ok"
            }
            mock_update.assert_called_once_with("my-proxy", workspace_label="label-1")

    @pytest.mark.asyncio
    async def test_update_proxy_description_too_long(self, proxy_client):
        with patch("api.proxy_routes.MAX_PROXY_CUSTOM_DESCRIPTION_CHARS", 10):
            res = await proxy_client.patch(
                "/api/proxies/session_gated/my-proxy",
                json={"customDescription": "123456789012345"},
                cookies={"session": "valid_token"}
            )
            assert res.status_code == 400
            assert res.json() == {"error": "invalid_description", "message": "customDescription too long"}

