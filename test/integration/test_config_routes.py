import pytest
from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
async def test_list_llm_pool_route(client):
    mock_entries = [
        {"id": "uuid-1", "name": "Model 1", "kind": "chat", "provider": "openai", "model": "gpt-4o", "is_active": True, "has_api_key": True}
    ]
    mock_active = {"chat": "uuid-1", "embedding": "uuid-2"}
    
    with patch("core.llm_config_service.list_pool", new_callable=AsyncMock, return_value=mock_entries), \
         patch("core.llm_config_service.get_active_map", new_callable=AsyncMock, return_value=mock_active):
        
        res = await client.get("/api/config/session_gated/llm/pool")
        assert res.status_code == 200
        data = res.json()
        assert data["entries"] == mock_entries
        assert data["active"] == mock_active


@pytest.mark.asyncio
async def test_add_llm_pool_entry_route(client):
    mock_entry = {"id": "uuid-new", "name": "New Model", "kind": "chat", "provider": "openai", "model": "gpt-4o"}
    
    payload = {
        "name": "New Model",
        "provider": "openai",
        "model": "gpt-4o",
        "kind": "chat"
    }
    
    with patch("core.llm_config_service.add_pool_entry", new_callable=AsyncMock, return_value=mock_entry):
        res = await client.post("/api/config/session_gated/llm/pool", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["entry"] == mock_entry


@pytest.mark.asyncio
async def test_add_llm_pool_entry_route_missing_fields(client):
    res = await client.post("/api/config/session_gated/llm/pool", json={"name": "Only Name"})
    assert res.status_code == 400
    assert "required" in res.json()["error"]


@pytest.mark.asyncio
async def test_delete_llm_pool_entry_route(client):
    with patch("core.llm_config_service.delete_pool_entry", new_callable=AsyncMock) as mock_delete:
        entry_id = "00000000-0000-0000-0000-000000000000"
        res = await client.delete(f"/api/config/session_gated/llm/pool/{entry_id}")
        assert res.status_code == 200
        assert res.json() == {"ok": True}
        mock_delete.assert_called_once_with(entry_id)


@pytest.mark.asyncio
async def test_set_llm_active_route(client):
    mock_active = {"chat": "uuid-active", "embedding": "uuid-emb"}
    
    with patch("core.llm_config_service.set_active", new_callable=AsyncMock, return_value=mock_active), \
         patch("core_graph.mcp_tool.invalidate_graph") as mock_invalidate:
        
        payload = {"kind": "chat", "id": "uuid-active"}
        res = await client.post("/api/config/session_gated/llm/pool/active", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["active"] == mock_active
        mock_invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_config_routes_non_dict_json(client):
    res = await client.post("/api/config/session_gated/llm", json=[1, 2, 3])
    assert res.status_code == 400
    assert res.json()["error"] == "invalid_json"

    res = await client.post("/api/config/session_gated/llm/pool", json="invalid")
    assert res.status_code == 400
    assert res.json()["error"] == "invalid_json"

    res = await client.post("/api/config/session_gated/llm/pool/active", json=[])
    assert res.status_code == 400
    assert res.json()["error"] == "invalid_json"


@pytest.mark.asyncio
async def test_active_toggle_route(client):
    with patch("core.llm_config_service.set_pool_entry_active", new_callable=AsyncMock) as mock_active_toggle, \
         patch("core_graph.mcp_tool.invalidate_graph") as mock_invalidate:
         
        payload = {"enabled": True}
        entry_id = "00000000-0000-0000-0000-000000000000"
        res = await client.post(f"/api/config/session_gated/llm/pool/{entry_id}/active-toggle", json=payload)
        assert res.status_code == 200
        assert res.json() == {"ok": True}
        mock_active_toggle.assert_called_once_with(entry_id, True)
        mock_invalidate.assert_called_once()
