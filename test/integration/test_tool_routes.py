import pytest
from unittest.mock import patch, AsyncMock

class TestToolRoutes:
    @pytest.mark.asyncio
    async def test_enable_tool_calls_set_enabled_and_show(self, client):
        with patch("api.tool_routes.set_tool_enabled", new_callable=AsyncMock) as mock_set_db, \
             patch("api.tool_routes.tool_visibility.show") as mock_show:
            
            res = await client.post("/api/plugins/session_gated/p1/tools/t1/enable")
            assert res.status_code == 200
            
            mock_set_db.assert_called_with("p1", "t1", True)
            mock_show.assert_called_with("t1")

    @pytest.mark.asyncio
    async def test_disable_tool_calls_hide(self, client):
        with patch("api.tool_routes.set_tool_enabled", new_callable=AsyncMock) as mock_set_db, \
             patch("api.tool_routes.tool_visibility.hide") as mock_hide:
            
            res = await client.delete("/api/plugins/session_gated/p1/tools/t1/enable")
            assert res.status_code == 200
            
            mock_set_db.assert_called_with("p1", "t1", False)
            mock_hide.assert_called_with("t1")

    @pytest.mark.asyncio
    async def test_db_error_returns_500(self, client):
        with patch("api.tool_routes.set_tool_enabled", new_callable=AsyncMock) as mock_set_db:
            mock_set_db.side_effect = Exception("DB error")
            res = await client.post("/api/plugins/session_gated/p1/tools/t1/enable")
            assert res.status_code == 500

    @pytest.mark.asyncio
    async def test_set_tool_embedding_model(self, client):
        with patch("api.tool_routes.set_tool_embedding_model", new_callable=AsyncMock) as mock_set_db:
            res = await client.post("/api/plugins/session_gated/p1/tools/t1/embedding-model", json={"model": "123"})
            assert res.status_code == 200
            mock_set_db.assert_called_with("p1", "t1", "123")

    @pytest.mark.asyncio
    async def test_clear_tool_embedding_model(self, client):
        with patch("api.tool_routes.set_tool_embedding_model", new_callable=AsyncMock) as mock_set_db:
            res = await client.post("/api/plugins/session_gated/p1/tools/t1/embedding-model", json={"model": "null"})
            assert res.status_code == 200
            mock_set_db.assert_called_with("p1", "t1", None)

    @pytest.mark.asyncio
    async def test_get_tool_permission(self, client):
        with patch("api.tool_routes.get_permission", new_callable=AsyncMock) as mock_get_db:
            mock_get_db.return_value = {"allow_read": True, "allow_write": False, "require_confirmation": True}
            res = await client.get("/api/plugins/session_gated/p1/tools/t1/permission")
            assert res.status_code == 200
            assert res.json() == {
                "plugin_id": "p1",
                "tool_name": "t1",
                "permission": {"allow_read": True, "allow_write": False, "require_confirmation": True}
            }
            mock_get_db.assert_called_with("p1", "t1")

    @pytest.mark.asyncio
    async def test_set_tool_permission_success(self, client):
        with patch("api.tool_routes.set_permission", new_callable=AsyncMock) as mock_set_db:
            mock_set_db.return_value = {"allow_read": False, "allow_write": True, "require_confirmation": False}
            res = await client.post(
                "/api/plugins/session_gated/p1/tools/t1/permission",
                json={"allow_read": False, "allow_write": True, "require_confirmation": False}
            )
            assert res.status_code == 200
            assert res.json() == {
                "plugin_id": "p1",
                "tool_name": "t1",
                "permission": {"allow_read": False, "allow_write": True, "require_confirmation": False}
            }
            mock_set_db.assert_called_with("p1", "t1", allow_read=False, allow_write=True, require_confirmation=False)

    @pytest.mark.asyncio
    async def test_set_tool_permission_validation_error(self, client):
        res = await client.post(
            "/api/plugins/session_gated/p1/tools/t1/permission",
            json={"allow_read": "not-a-boolean"}
        )
        assert res.status_code == 400
        assert res.json() == {"error": "allow_read must be a bool"}


