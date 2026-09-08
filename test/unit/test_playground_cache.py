import pytest
import asyncio
import time
from unittest.mock import patch, AsyncMock, MagicMock
from types import SimpleNamespace

import api.playground_routes as pr

@pytest.fixture(autouse=True)
def reset_cache():
    pr._tool_to_plugin_cache = {}
    pr._tool_to_plugin_cache_last_updated = 0.0
    pr._tool_to_plugin_lock = None

@pytest.mark.asyncio
async def test_cache_disabled_when_db_unavailable():
    """Verify that if _DB_AVAILABLE is False, it returns empty dict and does not query DB."""
    with patch("api.playground_routes._DB_AVAILABLE", False), \
         patch("db_layer.plugin_registry_store.DBPluginRegistry") as mock_registry:
        res = await pr._get_tool_plugin_mapping()
        assert res == {}
        mock_registry.assert_not_called()

@pytest.mark.asyncio
async def test_cache_hits_and_misses():
    """Verify cache miss queries DB once, and cache hit does not query DB again."""
    fake_record = SimpleNamespace(id="my_plugin", capabilities=["some_cap"])
    mock_registry_instance = MagicMock()
    mock_registry_instance.get_all = AsyncMock(return_value=[fake_record])

    with patch("api.playground_routes._DB_AVAILABLE", True), \
         patch("db_layer.plugin_registry_store.DBPluginRegistry", return_value=mock_registry_instance):
        
        # 1. First call (cache miss)
        res1 = await pr._get_tool_plugin_mapping()
        assert res1 == {"some_cap": "my_plugin"}
        assert mock_registry_instance.get_all.call_count == 1

        # 2. Second call (cache hit)
        res2 = await pr._get_tool_plugin_mapping()
        assert res2 == {"some_cap": "my_plugin"}
        # Call count remains 1 because it was fetched from cache
        assert mock_registry_instance.get_all.call_count == 1

@pytest.mark.asyncio
async def test_concurrent_thundering_herd():
    """Verify that concurrent requests serialize on the lock and only query the DB once."""
    fake_record = SimpleNamespace(id="my_plugin", capabilities=["some_cap"])
    
    call_count = 0
    async def slow_get_all():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return [fake_record]

    mock_registry_instance = MagicMock()
    mock_registry_instance.get_all = AsyncMock(side_effect=slow_get_all)

    with patch("api.playground_routes._DB_AVAILABLE", True), \
         patch("db_layer.plugin_registry_store.DBPluginRegistry", return_value=mock_registry_instance):
        
        # Trigger three concurrent cache lookups
        tasks = [
            pr._get_tool_plugin_mapping(),
            pr._get_tool_plugin_mapping(),
            pr._get_tool_plugin_mapping(),
        ]
        results = await asyncio.gather(*tasks)

        # All of them should get the correct mapping
        for res in results:
            assert res == {"some_cap": "my_plugin"}

        # But get_all should have been called exactly once!
        assert call_count == 1
