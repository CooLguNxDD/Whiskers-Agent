"""Unit tests: PluginAuthRegistry direct-login SSRF pre-check."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.plugin_loader.plugin_auth_registry import PluginAuthRegistry


@pytest.mark.asyncio
async def test_direct_login_rejects_unsafe_login_url():
    """_direct_login_and_persist must refuse private/loopback login_url."""
    mock_registry = MagicMock()
    mock_registry.events.on = MagicMock()
    auth = PluginAuthRegistry(registry=mock_registry)
    auth._http = AsyncMock()

    config = {
        "username": "u",
        "password": "p",
        "login_url": "http://127.0.0.1/login",
    }

    with patch(
        "core.plugin_loader.plugin_auth_registry._is_safe_url",
        new=AsyncMock(return_value=False),
    ) as mock_safe:
        token = await auth._direct_login_and_persist("test_plugin", config)

    assert token is None
    mock_safe.assert_awaited_once_with("http://127.0.0.1/login")
    auth._http.post.assert_not_called()


@pytest.mark.asyncio
async def test_direct_login_posts_when_url_safe():
    """Safe login_url proceeds to POST and returns the token value."""
    mock_registry = MagicMock()
    mock_registry.events.on = MagicMock()
    auth = PluginAuthRegistry(registry=mock_registry)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"token": {"value": "tok-abc", "expires_in": 3600}}
    auth._http = AsyncMock()
    auth._http.post = AsyncMock(return_value=mock_resp)
    auth._vault_write_direct_token = AsyncMock()

    config = {
        "username": "u",
        "password": "p",
        "login_url": "https://example.com/login",
    }

    with patch(
        "core.plugin_loader.plugin_auth_registry._is_safe_url",
        new=AsyncMock(return_value=True),
    ):
        token = await auth._direct_login_and_persist("test_plugin", config)

    assert token == "tok-abc"
    auth._http.post.assert_awaited_once()
