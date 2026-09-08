import pytest
from unittest.mock import MagicMock
from core.plugin_loader.plugin_auth_registry import PluginAuthRegistry, is_auth_error
from core.plugin_loader.auth_status import AuthStatus


def test_is_auth_error_detects_the_degraded_shape():
    assert is_auth_error({"status": "error", "error": "circular_auth_delegation"}) is True
    assert is_auth_error({"error": "auth_failed"}) is True
    assert is_auth_error({}) is False
    assert is_auth_error({"Authorization": "Bearer x"}) is False
    assert is_auth_error(None) is False
    assert is_auth_error("not a dict") is False

@pytest.mark.asyncio
async def test_auth_registry_clear_needs_reauth_on_success():
    # Create a dummy/mock registry
    mock_registry = MagicMock()
    auth_registry = PluginAuthRegistry(mock_registry)

    plugin_id = "plugin_x"

    # Define a successful auth provider
    async def mock_success_provider():
        return {"Authorization": "Bearer mock_token"}

    # Register the successful provider
    auth_registry.register_auth(plugin_id, mock_success_provider)

    # Mark as needing reauth
    auth_registry.mark_needs_reauth(plugin_id)
    assert auth_registry.get_auth_status(plugin_id) == AuthStatus.NEEDS_REAUTH
    assert plugin_id in auth_registry._needs_reauth

    # Call get_auth_headers, which should clear needs_reauth
    headers = await auth_registry.get_auth_headers(plugin_id)
    assert headers == {"Authorization": "Bearer mock_token"}
    assert auth_registry.get_auth_status(plugin_id) == AuthStatus.OK
    assert plugin_id not in auth_registry._needs_reauth

    # Clean up the registry http client
    await auth_registry.aclose()


@pytest.mark.asyncio
async def test_auth_registry_keep_needs_reauth_on_failure():
    mock_registry = MagicMock()
    auth_registry = PluginAuthRegistry(mock_registry)

    plugin_id = "plugin_fail"

    # Define an error auth provider
    async def mock_fail_provider():
        return {"status": "error", "error": "auth_failed"}

    # Register the failed provider
    auth_registry.register_auth(plugin_id, mock_fail_provider)

    # Mark as needing reauth
    auth_registry.mark_needs_reauth(plugin_id)
    assert auth_registry.get_auth_status(plugin_id) == AuthStatus.NEEDS_REAUTH

    # Call get_auth_headers, which should NOT clear needs_reauth
    headers = await auth_registry.get_auth_headers(plugin_id)
    assert headers == {"status": "error", "error": "auth_failed"}
    assert auth_registry.get_auth_status(plugin_id) == AuthStatus.NEEDS_REAUTH
    assert plugin_id in auth_registry._needs_reauth

    # Clean up the registry http client
    await auth_registry.aclose()


@pytest.mark.asyncio
async def test_auth_registry_delegation_clear_needs_reauth():
    mock_registry = MagicMock()
    auth_registry = PluginAuthRegistry(mock_registry)

    parent_id = "parent_plugin"
    child_id = "child_plugin"

    # Parent succeeds
    async def mock_parent_provider():
        return {"Authorization": "Bearer parent_token"}

    auth_registry.register_auth(parent_id, mock_parent_provider)
    # Child delegates to parent
    auth_registry.set_auth_delegate(child_id, parent_id)

    # Mark both as needing reauth
    auth_registry.mark_needs_reauth(parent_id)
    auth_registry.mark_needs_reauth(child_id)

    # Call get_auth_headers for child
    headers = await auth_registry.get_auth_headers(child_id)
    assert headers == {"Authorization": "Bearer parent_token"}

    # Both parent and child should have needs_reauth cleared
    assert parent_id not in auth_registry._needs_reauth
    assert child_id not in auth_registry._needs_reauth

    # Clean up the registry http client
    await auth_registry.aclose()


@pytest.mark.asyncio
async def test_auth_registry_api_token_resolution():
    mock_vault = MagicMock()
    async def mock_vault_get(plugin_id, key):
        if plugin_id == "plugin_api" and key == "api_token":
            return "mock_api_token"
        return None
    mock_vault.get = mock_vault_get

    mock_registry = MagicMock()
    mock_registry._vault = mock_vault
    mock_registry._relay = None

    auth_registry = PluginAuthRegistry(mock_registry)
    plugin_id = "plugin_api"
    config = {"auth_header": "X-API-Token"}

    auth_registry.register_direct_auth(plugin_id, config)

    headers = await auth_registry.get_auth_headers(plugin_id)
    assert headers == {"X-API-Token": "mock_api_token"}

    await auth_registry.aclose()


@pytest.mark.asyncio
async def test_circular_delegation_degrades_instead_of_raising():
    mock_registry = MagicMock()
    auth_registry = PluginAuthRegistry(mock_registry)

    auth_registry.set_auth_delegate("a", "b")
    auth_registry.set_auth_delegate("b", "a")

    headers = await auth_registry.get_auth_headers("a")
    assert is_auth_error(headers)
    assert headers["error"] == "circular_auth_delegation"

    await auth_registry.aclose()


@pytest.mark.asyncio
async def test_no_provider_registered_degrades_instead_of_raising():
    mock_registry = MagicMock()
    auth_registry = PluginAuthRegistry(mock_registry)

    headers = await auth_registry.get_auth_headers("nobody_registered_this")
    assert is_auth_error(headers)
    assert headers["error"] == "no_auth_registered"

    await auth_registry.aclose()

