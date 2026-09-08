import os
import pytest
import pytest_asyncio
import httpx
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture(scope="session")
def mock_vault():
    v = AsyncMock()
    v.get = AsyncMock(return_value=None)
    v.set = AsyncMock(return_value=None)
    v.exists = AsyncMock(return_value=False)
    v.get_required = AsyncMock(side_effect=Exception("missing"))
    return v

@pytest.fixture(scope="session")
def mock_oauth_svc():
    svc = AsyncMock()
    svc.validate_token = AsyncMock(return_value={
        "sub": "admin", "scopes": ["admin"], "exp": 9999999999, "iat": 1700000000, "jti": "test-jti"
    })
    svc._issue_token_pair = AsyncMock(return_value={
        "access_token": "acc.tok.en", "refresh_token": "ref.tok.en"
    })
    svc.revoke_token = AsyncMock(return_value=None)
    svc.refresh_grant = AsyncMock(return_value={
        "access_token": "new.acc", "refresh_token": "new.ref"
    })
    return svc

@pytest.fixture(scope="session")
def mock_oauth_provider(mock_oauth_svc):
    prov = MagicMock()
    prov._svc = mock_oauth_svc
    return prov

@pytest_asyncio.fixture(scope="session")
async def test_app(mock_vault, mock_oauth_provider):
    import core.context
    core.context.oauth_provider = mock_oauth_provider
    import api.middleware
    api.middleware.oauth_provider = mock_oauth_provider
    import api.admin_routes  # noqa — triggers @mcp.custom_route registration
    import api.plugin_routes
    import api.tool_routes
    import api.config_routes
    import api.route_routes
    import api.proxy_routes
    import api.api_key_routes
    import api.analytics_routes
    from core.context import mcp, http_route_registry
    from api.middleware import PublicPathMiddleware, SessionGateMiddleware

    app = mcp.http_app(path="/mcp")
    http_route_registry.bind_app(app)
    app = PublicPathMiddleware(app)
    app = SessionGateMiddleware(app)
    return app

@pytest_asyncio.fixture
async def client(test_app, mock_vault, mock_oauth_provider, mock_oauth_svc):
    # Patch module-level imports in each route module (bound at import time)
    from unittest.mock import patch
    admin_payload = {
        "sub": "admin", "scopes": ["admin"], "exp": 9999999999, "iat": 1700000000, "jti": "test-jti"
    }
    with patch.object(mock_oauth_svc, "validate_token", AsyncMock(return_value=admin_payload)), \
         patch("api.admin_routes.vault", mock_vault), \
         patch("api.admin_routes.oauth_provider", mock_oauth_provider), \
         patch("api.middleware.oauth_provider", mock_oauth_provider), \
         patch("core.context.oauth_provider", mock_oauth_provider), \
         patch("api.proxy_routes._validate_session_cookie", AsyncMock(return_value=admin_payload)):
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies={"session": "admin_session_token"}) as c:
            yield c

@pytest_asyncio.fixture
async def non_admin_client(test_app, mock_vault, mock_oauth_provider, mock_oauth_svc):
    """Client authenticated as a viewer role with only read tokens (no admin bypass)."""
    from unittest.mock import patch
    viewer_payload = {
        "sub": "viewer_user",
        "scopes": [
            "core:whiskers.console:read",
            "core:whiskers.plugins:read",
            "core:whiskers.proxy:read",
            "core:whiskers.analytics:read",
            "core:graph:read",
            "core:config:read",
            "core:apikey:read",
        ],
        "ocat_role": "viewer",
        "ocat_tenant": 1,
        "exp": 9999999999,
        "iat": 1700000000,
        "jti": "test-viewer-jti",
    }
    with patch.object(mock_oauth_svc, "validate_token", AsyncMock(return_value=viewer_payload)), \
         patch("api.admin_routes.vault", mock_vault), \
         patch("api.admin_routes.oauth_provider", mock_oauth_provider), \
         patch("api.middleware.oauth_provider", mock_oauth_provider), \
         patch("core.context.oauth_provider", mock_oauth_provider), \
         patch("api.proxy_routes._validate_session_cookie", AsyncMock(return_value=viewer_payload)):
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"session": "viewer_session_token"},
        ) as c:
            yield c

