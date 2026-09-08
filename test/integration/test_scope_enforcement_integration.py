import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_non_admin_client_enforcement_apikeys(non_admin_client):
    with patch("api.api_key_routes.list_api_keys", new_callable=AsyncMock, return_value=[]):
        # GET list is read (core:apikey:read) -> 200
        res_get = await non_admin_client.get("/api/auth/session_gated/api-keys")
        assert res_get.status_code == 200

    # POST create is write (core:apikey:write) -> 403 (viewer only has core:apikey:read)
    res_post = await non_admin_client.post("/api/auth/session_gated/api-keys", json={"name": "test-key"})
    assert res_post.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_client_enforcement_routes(non_admin_client):
    with patch("api.route_routes.route_store.list_route_groups", new_callable=AsyncMock, return_value=[]):
        # GET list routes is read (core:config:read) -> 200
        res_get = await non_admin_client.get("/api/routes/session_gated")
        assert res_get.status_code == 200

    # POST reindex is write (core:config:write) -> 403 (viewer only has core:config:read)
    res_post = await non_admin_client.post("/api/routes/session_gated/reindex")
    assert res_post.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_client_enforcement_proxies(non_admin_client):
    with patch("api.proxy_routes.proxy_manager.list_proxies", new_callable=AsyncMock, return_value=[]):
        # GET list proxies is read (core:whiskers.proxy:read) -> 200
        res_get = await non_admin_client.get("/api/proxies/session_gated")
        assert res_get.status_code == 200

    # POST add proxy is write (core:whiskers.proxy:write) -> 403 (viewer only has core:whiskers.proxy:read)
    res_post = await non_admin_client.post("/api/proxies/session_gated", json={"name": "test_proxy", "sourceType": "stdio", "command": "echo"})
    assert res_post.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_client_enforcement_plugins(non_admin_client):
    with patch("api.plugin_routes.registry.get_registry") as mock_get_reg:
        mock_reg = AsyncMock()
        mock_reg.lifecycle._plugins = []
        mock_reg.system_tier = 1
        mock_get_reg.return_value = mock_reg
        # GET list plugins is read (core:whiskers.plugins:read) -> 200
        res_get = await non_admin_client.get("/api/plugins/session_gated")
        assert res_get.status_code == 200

    # POST prune-stale is write (core:whiskers.plugins:write) -> 403 (viewer only has core:whiskers.plugins:read)
    res_post = await non_admin_client.post("/api/plugins/session_gated/prune-stale")
    assert res_post.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_client_enforcement_analytics(non_admin_client):
    # GET summary is read (core:whiskers.analytics:read) -> 200
    res_get = await non_admin_client.get("/api/analytics/session_gated/summary")
    assert res_get.status_code == 200
