import pytest
from unittest.mock import AsyncMock, MagicMock
from pydantic import AnyUrl
from mcp.shared.auth import OAuthClientInformationFull
from oauth.oauth_service import OAuthService, OAuthService_FastMCPProvider


@pytest.mark.asyncio
async def test_oauth_service_auto_register_client_new():
    mock_svc = AsyncMock(spec=OAuthService)
    mock_svc.get_client.return_value = None

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=["whiskers", "terminal:use", "terminal:host"],
    )

    client_id = "test-client-id"
    redirect_uri = "http://localhost/callback"
    await provider.auto_register_client(client_id, redirect_uri)

    mock_svc.register_client.assert_called_once()
    called_client = mock_svc.register_client.call_args[0][0]
    assert isinstance(called_client, OAuthClientInformationFull)
    assert called_client.client_id == client_id
    assert called_client.scope == "whiskers terminal:use terminal:host"
    assert AnyUrl(redirect_uri) in called_client.redirect_uris


@pytest.mark.asyncio
async def test_oauth_service_auto_register_client_existing_same_scopes():
    mock_svc = AsyncMock(spec=OAuthService)
    redirect_uri = "http://localhost/callback"
    existing_client = OAuthClientInformationFull(
        client_id="test-client-id",
        redirect_uris=[AnyUrl(redirect_uri)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope="whiskers terminal:use terminal:host",
    )
    mock_svc.get_client.return_value = existing_client

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=["whiskers", "terminal:use", "terminal:host"],
    )

    await provider.auto_register_client("test-client-id", redirect_uri)
    # Since existing redirect URI and scopes match, register_client should NOT be called again
    mock_svc.register_client.assert_not_called()


@pytest.mark.asyncio
async def test_oauth_service_auto_register_client_existing_diff_scopes():
    mock_svc = AsyncMock(spec=OAuthService)
    redirect_uri = "http://localhost/callback"
    # Client registered with old scope 'whiskers'
    existing_client = OAuthClientInformationFull(
        client_id="test-client-id",
        redirect_uris=[AnyUrl(redirect_uri)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope="whiskers",
    )
    mock_svc.get_client.return_value = existing_client

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=["whiskers", "terminal:use", "terminal:host"],
    )

    await provider.auto_register_client("test-client-id", redirect_uri)
    # Since scopes differ, register_client SHOULD be called to update the client's scopes
    mock_svc.register_client.assert_called_once()
    called_client = mock_svc.register_client.call_args[0][0]
    assert called_client.scope == "whiskers terminal:use terminal:host"


import os

@pytest.mark.skipif(not os.environ.get("MASTER_KEY"), reason="requires MASTER_KEY and database")
@pytest.mark.asyncio
async def test_sweep_db_pending_auths():
    from db_layer.connection import get_async_session
    from sqlalchemy import text

    # 1. Clean up first
    async with get_async_session() as session:
        await session.execute(text("DELETE FROM mcp_pending_auths"))
        await session.commit()

    service = OAuthService()

    # 2. Insert an expired one directly via raw SQL
    async with get_async_session() as session:
        await session.execute(
            text(
                "INSERT INTO mcp_pending_auths (auth_state, data, expires_at) "
                "VALUES (:state, :data, now() - INTERVAL '1 minute')"
            ),
            {"state": "expired_state", "data": '{"test": "expired"}'}
        )
        await session.commit()

    # Insert a fresh one using the service
    await service.save_pending_auth("fresh_state", {"test": "fresh"})

    # 3. Perform sweep
    deleted_count = await service.sweep_db_pending_auths()
    assert deleted_count == 1

    # 4. Verify DB state: expired_state should be deleted, fresh_state should remain
    expired_pop = await service.pop_pending_auth("expired_state")
    assert expired_pop is None

    fresh_pop = await service.pop_pending_auth("fresh_state")
    assert fresh_pop == {"test": "fresh"}


@pytest.mark.asyncio
async def test_provider_authorize_fires_sweep_task():
    import asyncio
    from mcp.server.auth.provider import AuthorizationParams

    # We mock sweep_db_pending_auths on the service to make sure it gets called
    mock_svc = AsyncMock(spec=OAuthService)
    mock_svc.sweep_db_pending_auths.return_value = 0

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=["whiskers"],
    )

    client = OAuthClientInformationFull(
        client_id="test-client-id",
        redirect_uris=[AnyUrl("http://localhost/callback")],
        grant_types=["authorization_code"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope="whiskers",
    )

    params = AuthorizationParams(
        response_type="code",
        client_id="test-client-id",
        redirect_uri=AnyUrl("http://localhost/callback"),
        redirect_uri_provided_explicitly=True,
        state="test-state",
        code_challenge="test-challenge",
        code_challenge_method="S256",
        scopes=["whiskers"],
    )

    # Call authorize
    await provider.authorize(client, params)

    # Since sweep_db_pending_auths is fired via asyncio.create_task, we should yield control
    # to the event loop so the task has a chance to execute.
    await asyncio.sleep(0.05)

    # Verify that sweep_db_pending_auths was called
    mock_svc.sweep_db_pending_auths.assert_called_once()

