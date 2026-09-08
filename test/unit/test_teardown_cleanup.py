import pytest
from unittest.mock import AsyncMock, MagicMock
import db_layer.connection as connection
from core.plugin_loader.plugin_lifecycle_registry import PluginLifecycleRegistry


@pytest.mark.asyncio
async def test_dispose_async_engine():
    """Verify dispose_async_engine() calls engine.dispose() and nils both globals."""
    mock_engine = AsyncMock()
    mock_factory = MagicMock()

    # Save original globals to restore them later
    orig_engine = connection._async_engine
    orig_factory = connection._async_session_factory

    try:
        connection._async_engine = mock_engine
        connection._async_session_factory = mock_factory

        await connection.dispose_async_engine()

        mock_engine.dispose.assert_awaited_once()
        assert connection._async_engine is None
        assert connection._async_session_factory is None
    finally:
        connection._async_engine = orig_engine
        connection._async_session_factory = orig_factory


@pytest.mark.asyncio
async def test_teardown_plugins_clears_state():
    """Verify teardown_plugins() unloads plugins, clears all containers, and resets loaded status."""
    mock_registry = MagicMock()
    registry_lifecycle = PluginLifecycleRegistry(mock_registry)

    mock_plugin = AsyncMock()
    mock_plugin.name = "test_plugin"
    mock_plugin.on_unload = AsyncMock()

    # Populate containers
    registry_lifecycle._plugins.append(mock_plugin)
    registry_lifecycle._plugin_contexts["test_plugin"] = MagicMock()
    registry_lifecycle._plugin_id_map["test_plugin"] = mock_plugin
    registry_lifecycle._plugin_locks["test_plugin"] = MagicMock()
    registry_lifecycle._loaded = True

    # Invoke teardown
    await registry_lifecycle.teardown_plugins()

    # Verify on_unload was called
    mock_plugin.on_unload.assert_awaited_once()

    # Verify everything was cleared/reset
    assert len(registry_lifecycle._plugins) == 0
    assert len(registry_lifecycle._plugin_contexts) == 0
    assert len(registry_lifecycle._plugin_id_map) == 0
    assert len(registry_lifecycle._plugin_locks) == 0
    assert registry_lifecycle._loaded is False


@pytest.mark.asyncio
async def test_get_async_session_factory_failure():
    """Verify that _get_async_session_factory resets globals and reraises on failure."""
    from unittest.mock import patch

    # Save original globals
    orig_engine = connection._async_engine
    orig_factory = connection._async_session_factory

    # Reset globals for the test
    connection._async_engine = None
    connection._async_session_factory = None

    try:
        mock_engine = MagicMock()
        # Patch dependencies so create_async_engine succeeds but async_sessionmaker raises
        with patch("db_layer.connection.create_async_engine", return_value=mock_engine), \
             patch("db_layer.connection.get_database_url", return_value="postgresql://localhost/db"), \
             patch("db_layer.connection._validate_master_key"), \
             patch("db_layer.connection.event.listen"), \
             patch("db_layer.connection.async_sessionmaker", side_effect=ValueError("Simulated factory error")):
            
            with pytest.raises(ValueError, match="Simulated factory error"):
                await connection._get_async_session_factory()
            
            # Verify both were reset to None on exception
            assert connection._async_engine is None
            assert connection._async_session_factory is None
    finally:
        # Restore original globals
        connection._async_engine = orig_engine
        connection._async_session_factory = orig_factory
