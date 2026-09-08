"""Unit tests for the ProxyManager database storage, validation, and connectivity lifecycle."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select, delete

import httpx

from core.proxy.proxy_manager import proxy_manager, _is_safe_url, _SSRFSafeTransport
from core.proxy.models import ProxyServer
from db_layer.connection import get_async_session
from db_layer.models import PluginCredential
from core.context import vault


@pytest.fixture(autouse=True)
async def db_cleanup():
    """Autouse fixture to clean up database state before and after every test."""
    await do_cleanup()
    yield
    await do_cleanup()


async def do_cleanup():
    async with get_async_session() as session:
        # Delete test proxy servers starting with 'test-pm-'
        await session.execute(
            delete(ProxyServer).where(ProxyServer.name.like("test-pm-%"))
        )
        # Delete vault credentials starting with 'proxy_test-pm-'
        await session.execute(
            delete(PluginCredential).where(
                PluginCredential.plugin_id.like("proxy_test-pm-%")
            )
        )
        # Delete plugins table rows starting with 'proxy_test-pm-'
        from sqlalchemy import text
        await session.execute(
            text("DELETE FROM plugins WHERE id LIKE 'proxy_test-pm-%'")
        )
        await session.commit()


@pytest.fixture(autouse=True)
def mock_mount_unmount():
    """Autouse fixture to mock mount/unmount and prevent side effects on the global mcp object."""
    with patch("core.proxy.proxy_manager.mount_proxy") as m_mount, \
         patch("core.proxy.proxy_manager.unmount_proxy") as m_unmount:
        yield m_mount, m_unmount


@pytest.mark.asyncio
async def test_add_proxy_invalid_inputs():
    with patch("core.proxy.ssrf_safety._is_safe_url", return_value=True):
        # 1. Invalid name format
        with pytest.raises(ValueError, match="Invalid name format"):
            await proxy_manager.add_proxy("invalid name!", "http", "http://localhost")

        # 2. Invalid transport
        with pytest.raises(ValueError, match="Invalid transport"):
            await proxy_manager.add_proxy("test-pm-invalid", "tcp", "http://localhost")

        # 3. Invalid URL scheme
        with pytest.raises(ValueError, match="URL must start with"):
            await proxy_manager.add_proxy("test-pm-invalid", "http", "ftp://localhost")

@pytest.mark.asyncio
async def test_add_proxy_ssrf_prevention():
    # 4. URL is restricted — ensure the dev-mode bypass is off
    with patch.dict("os.environ", {"CAT_ALLOW_LOCAL_PROXIES": ""}, clear=False):
        with pytest.raises(ValueError, match="URL 'http://localhost:10000' is restricted"):
            await proxy_manager.add_proxy("test-pm-invalid", "http", "http://localhost:10000")

        with pytest.raises(ValueError, match="URL 'http://169.254.169.254' is restricted"):
            await proxy_manager.add_proxy("test-pm-invalid", "http", "http://169.254.169.254")

@pytest.mark.asyncio
async def test_is_safe_url():
    with patch.dict("os.environ", {"CAT_ALLOW_LOCAL_PROXIES": ""}, clear=False):
        assert await _is_safe_url("http://example.com") is True
        assert await _is_safe_url("https://google.com") is True
        assert await _is_safe_url("http://localhost:10000") is False
        assert await _is_safe_url("http://127.0.0.1:10000") is False
        assert await _is_safe_url("http://192.168.1.100") is False
        assert await _is_safe_url("http://10.0.0.1") is False
        assert await _is_safe_url("http://169.254.169.254") is False
        assert await _is_safe_url("http://[::1]") is False
        assert await _is_safe_url("http://0.0.0.0") is False
        assert await _is_safe_url("http://0.0.0.0:10000") is False


@pytest.mark.asyncio
async def test_is_safe_url_rejects_ipv4_mapped_ipv6():
    # ::ffff:127.0.0.1 is the IPv4-mapped IPv6 form of 127.0.0.1; dual-stack sockets
    # transparently route it to the IPv4 loopback target, so it must be rejected too.
    with patch.dict("os.environ", {"CAT_ALLOW_LOCAL_PROXIES": ""}, clear=False):
        assert await _is_safe_url("http://[::ffff:127.0.0.1]") is False
        assert await _is_safe_url("http://[::ffff:169.254.169.254]") is False


@pytest.mark.asyncio
async def test_ssrf_transport_blocks_unsafe_resolved_host():
    transport = _SSRFSafeTransport()
    request = httpx.Request("GET", "http://internal.example.com/")
    with patch("core.proxy.ssrf_safety._resolve_safe_ip", AsyncMock(return_value=None)):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(request)


@pytest.mark.asyncio
async def test_ssrf_transport_pins_validated_ip_and_keeps_sni():
    transport = _SSRFSafeTransport()
    mock_response = httpx.Response(200)

    with patch.dict("os.environ", {"CAT_ALLOW_LOCAL_PROXIES": ""}, clear=False), \
         patch("core.proxy.ssrf_safety._resolve_safe_ip", AsyncMock(return_value="93.184.216.34")), \
         patch.object(httpx.AsyncHTTPTransport, "handle_async_request", AsyncMock(return_value=mock_response)) as mock_super:
        request = httpx.Request("GET", "https://example.com/")
        result = await transport.handle_async_request(request)

        assert result is mock_response
        sent_request = mock_super.call_args[0][0]
        # The actual connection targets the validated IP...
        assert sent_request.url.host == "93.184.216.34"
        # ...but TLS SNI/cert validation still uses the original hostname.
        assert sent_request.extensions.get("sni_hostname") == "example.com"


@pytest.mark.asyncio
async def test_ssrf_transport_allows_local_override():
    transport = _SSRFSafeTransport()
    request = httpx.Request("GET", "http://localhost:9000/")
    mock_response = httpx.Response(200)

    with patch.dict("os.environ", {"CAT_ALLOW_LOCAL_PROXIES": "1"}), \
         patch.object(httpx.AsyncHTTPTransport, "handle_async_request", AsyncMock(return_value=mock_response)) as mock_super:
        result = await transport.handle_async_request(request)

        assert result is mock_response
        sent_request = mock_super.call_args[0][0]
        # Unmodified when the local override is set.
        assert sent_request.url.host == "localhost"


@pytest.mark.asyncio
async def test_add_proxy_connection_failure():
    with patch("core.proxy.ssrf_safety._is_safe_url", return_value=True):
        with patch("core.proxy.proxy_manager.build_proxy") as mock_build:
            mock_provider = MagicMock()
            mock_server = AsyncMock()
            mock_server.list_tools.side_effect = Exception("Connection timed out")
            mock_provider.server = mock_server
            mock_build.return_value = mock_provider

            with pytest.raises(ValueError, match="Upstream MCP server connection failed during discovery"):
                await proxy_manager.add_proxy("test-pm-fail", "http", "http://localhost:9000", auth_mode="none")


@pytest.mark.asyncio
async def test_add_proxy_success_with_vault_bearer():
    with patch("core.proxy.ssrf_safety._is_safe_url", return_value=True):
        with patch("core.proxy.proxy_manager.build_proxy") as mock_build:
            mock_provider = MagicMock()
            mock_server = AsyncMock()
            mock_server.list_tools.return_value = [MagicMock(name="tool1")]
            mock_provider.server = mock_server
            mock_build.return_value = mock_provider

            res = await proxy_manager.add_proxy(
                name="test-pm-success",
                transport="http",
                url="http://localhost:9000",
                auth_mode="bearer",
                bearer_token="secret-token"
            )

            assert res["name"] == "test-pm-success"
            assert res["transport"] == "http"
            assert res["url"] == "http://localhost:9000"
            assert res["status"] == "active"
            assert res["authMode"] == "bearer"
            assert res["toolCount"] == 1
            assert res["errorMessage"] is None

        # Check DB directly
        async with get_async_session() as session:
            stmt = select(ProxyServer).where(ProxyServer.name == "test-pm-success")
            proxy_row = (await session.execute(stmt)).scalar_one_or_none()
            assert proxy_row is not None
            assert proxy_row.status == "active"
            assert proxy_row.tool_count == 1
            assert proxy_row.auth_mode == "bearer"

        # Check Vault directly
        token = await vault.get("proxy_test-pm-success", "bearer")
        assert token == "secret-token"


@pytest.mark.asyncio
async def test_add_proxy_oauth_setup():
    oauth_config = {
        "authorize_url": "https://id.provider.com/authorize",
        "token_url": "https://id.provider.com/token",
        "client_id": "test-client-id",
        "scopes": ["read", "write"]
    }
    
    with patch("core.proxy.ssrf_safety._is_safe_url", return_value=True), \
         patch("core.context.oauth_relay") as mock_relay:
        mock_relay.upsert_manifest = AsyncMock()
        res = await proxy_manager.add_proxy(
            name="test-pm-oauth",
            transport="http",
            url="http://localhost:9000",
            auth_mode="oauth",
            oauth_config=oauth_config,
            client_secret="my-client-secret"
        )

        # Assert status is inactive initially since it needs client login
        assert res["name"] == "test-pm-oauth"
        assert res["status"] == "inactive"
        assert res["authMode"] == "oauth"
        assert res["oauthStatus"] == "not_connected"
        assert res["toolCount"] == 0

        # Verify client secret in vault
        secret = await vault.get("proxy_test-pm-oauth", "UPSTREAM_CLIENT_SECRET")
        assert secret == "my-client-secret"

        # Verify synthetic manifest upserted into relay
        mock_relay.upsert_manifest.assert_called_once()
        called_args, _ = mock_relay.upsert_manifest.call_args
        assert called_args[0] == "proxy_test-pm-oauth"
        manifest = called_args[1]
        assert manifest["name"] == "proxy_test-pm-oauth"
        assert manifest["external_oauth"]["upstream"]["client_id"] == "test-client-id"


@pytest.mark.asyncio
async def test_add_proxy_oauth_autodiscovery_and_dcr():
    with patch("core.proxy.ssrf_safety._is_safe_url", return_value=True), \
         patch("core.context.oauth_relay") as mock_relay, \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_relay.upsert_manifest = AsyncMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        
        resp1 = MagicMock()
        resp1.status_code = 401
        resp1.headers = {"www-authenticate": 'Bearer realm="mcp", ResourceMetadata="http://localhost:9000/metadata"'}
        
        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {
            "authorization_servers": ["https://auth.provider.com"]
        }
        
        resp3 = MagicMock()
        resp3.status_code = 200
        resp3.json.return_value = {
            "authorization_endpoint": "https://auth.provider.com/authorize",
            "token_endpoint": "https://auth.provider.com/token",
            "registration_endpoint": "https://auth.provider.com/register"
        }
        
        resp4 = MagicMock()
        resp4.status_code = 201
        resp4.json.return_value = {
            "client_id": "auto-discovered-client-id",
            "client_secret": "auto-registered-client-secret"
        }
        
        mock_client.get.side_effect = [resp1, resp2, resp3]
        mock_client.post.side_effect = [resp4]
        
        res = await proxy_manager.add_proxy(
            name="test-pm-auto",
            transport="http",
            url="http://localhost:9000",
            auth_mode="oauth"
        )
        
        assert res["name"] == "test-pm-auto"
        assert res["status"] == "inactive"
        assert res["authMode"] == "oauth"
        assert res["oauthStatus"] == "not_connected"
        
        # Verify DCR secrets in vault
        secret = await vault.get("proxy_test-pm-auto", "UPSTREAM_CLIENT_SECRET")
        assert secret == "auto-registered-client-secret"
        
        # Verify synthetic manifest upserted into relay with autodiscovered properties
        mock_relay.upsert_manifest.assert_called_once()
        called_args, _ = mock_relay.upsert_manifest.call_args
        manifest = called_args[1]
        assert manifest["name"] == "proxy_test-pm-auto"
        assert manifest["external_oauth"]["upstream"]["client_id"] == "auto-discovered-client-id"
        assert manifest["external_oauth"]["upstream"]["authorize_url"] == "https://auth.provider.com/authorize"
        assert manifest["external_oauth"]["upstream"]["token_url"] == "https://auth.provider.com/token"


@pytest.mark.asyncio
async def test_add_proxy_duplicate_name():
    with patch("core.proxy.ssrf_safety._is_safe_url", return_value=True), \
         patch("core.proxy.proxy_manager.build_proxy") as mock_build:
        mock_provider = MagicMock()
        mock_server = AsyncMock()
        mock_server.list_tools.return_value = []
        mock_provider.server = mock_server
        mock_build.return_value = mock_provider

        # First add
        await proxy_manager.add_proxy("test-pm-dup", "http", "http://localhost", auth_mode="none")

        # Second add with same name should raise duplicate error
        with pytest.raises(ValueError, match="already exists"):
            await proxy_manager.add_proxy("test-pm-dup", "http", "http://localhost", auth_mode="none")


@pytest.mark.asyncio
async def test_list_proxies():
    with patch("core.proxy.ssrf_safety._is_safe_url", return_value=True), \
         patch("core.proxy.proxy_manager.build_proxy") as mock_build:
        mock_provider = MagicMock()
        mock_server = AsyncMock()
        mock_server.list_tools.return_value = []
        mock_provider.server = mock_server
        mock_build.return_value = mock_provider

        await proxy_manager.add_proxy("test-pm-b", "http", "http://localhost", auth_mode="none")
        await proxy_manager.add_proxy("test-pm-a", "sse", "http://localhost", auth_mode="none")

        list_res = await proxy_manager.list_proxies()
        
        # Filter only our test proxies
        filtered = [p for p in list_res if p["name"].startswith("test-pm-")]
        assert len(filtered) == 2
        
        # Verify alphabetical order
        assert filtered[0]["name"] == "test-pm-a"
        assert filtered[1]["name"] == "test-pm-b"


@pytest.mark.asyncio
async def test_remove_proxy_success():
    with patch("core.proxy.ssrf_safety._is_safe_url", return_value=True), \
         patch("core.proxy.proxy_manager.build_proxy") as mock_build:
        mock_provider = MagicMock()
        mock_server = AsyncMock()
        mock_server.list_tools.return_value = []
        mock_provider.server = mock_server
        mock_build.return_value = mock_provider

        await proxy_manager.add_proxy(
            name="test-pm-del",
            transport="http",
            url="http://localhost",
            auth_mode="bearer",
            bearer_token="some-token"
        )

        # Check in Vault
        assert await vault.get("proxy_test-pm-del", "bearer") == "some-token"

        res = await proxy_manager.remove_proxy("test-pm-del")
        assert res["ok"] is True

        # Check deleted from DB
        async with get_async_session() as session:
            stmt = select(ProxyServer).where(ProxyServer.name == "test-pm-del")
            assert (await session.execute(stmt)).scalar_one_or_none() is None

        # Check deleted from Vault
        assert await vault.get("proxy_test-pm-del", "bearer") is None


@pytest.mark.asyncio
async def test_remove_proxy_not_found():
    with pytest.raises(ValueError, match="not found"):
        await proxy_manager.remove_proxy("test-pm-nonexistent")


@pytest.mark.asyncio
async def test_test_proxy_success():
    with patch("core.proxy.proxy_manager.build_proxy") as mock_build:
        mock_provider = MagicMock()
        mock_server = AsyncMock()
        mock_server.list_tools.return_value = [MagicMock(name="tool")]
        mock_provider.server = mock_server
        mock_build.return_value = mock_provider

        # Setup an existing error-state proxy
        async with get_async_session() as session:
            db_row = ProxyServer(
                name="test-pm-test-succ",
                transport="http",
                url="http://localhost",
                status="error",
                has_auth=True,
                auth_mode="bearer",
                tool_count=0,
                error_message="Previous error message"
            )
            session.add(db_row)
            await session.commit()

        await proxy_manager._ensure_plugins_row("test-pm-test-succ")
        await vault.set("proxy_test-pm-test-succ", "bearer", "my-vault-token")

        res = await proxy_manager.test_proxy("test-pm-test-succ")
        assert res["status"] == "active"
        assert res["toolCount"] == 1
        assert res["errorMessage"] is None

        # Check updated DB values
        async with get_async_session() as session:
            stmt = select(ProxyServer).where(ProxyServer.name == "test-pm-test-succ")
            proxy_row = (await session.execute(stmt)).scalar_one()
            assert proxy_row.status == "active"
            assert proxy_row.tool_count == 1
            assert proxy_row.error_message is None


@pytest.mark.asyncio
async def test_test_proxy_connection_failure():
    with patch("core.proxy.proxy_manager.build_proxy") as mock_build:
        mock_provider = MagicMock()
        mock_server = AsyncMock()
        mock_server.list_tools.side_effect = Exception("Downstream network issue")
        mock_provider.server = mock_server
        mock_build.return_value = mock_provider

        # Setup an existing active proxy
        async with get_async_session() as session:
            db_row = ProxyServer(
                name="test-pm-test-fail",
                transport="http",
                url="http://localhost",
                status="active",
                has_auth=False,
                auth_mode="none",
                tool_count=10,
                error_message=None
            )
            session.add(db_row)
            await session.commit()

        res = await proxy_manager.test_proxy("test-pm-test-fail")
        assert res["status"] == "error"
        assert res["errorMessage"] == "Connection failed during discovery."

        # Check DB row is error
        async with get_async_session() as session:
            stmt = select(ProxyServer).where(ProxyServer.name == "test-pm-test-fail")
            proxy_row = (await session.execute(stmt)).scalar_one()
            assert proxy_row.status == "error"
            assert proxy_row.error_message == "Connection failed during discovery."


@pytest.mark.asyncio
async def test_load_persisted():
    with patch("core.proxy.proxy_manager.build_proxy") as mock_build:
        mock_provider = MagicMock()
        mock_server = AsyncMock()
        # Mocking 2 tools discovered
        mock_server.list_tools.return_value = [MagicMock(name="t1"), MagicMock(name="t2")]
        mock_provider.server = mock_server
        mock_build.return_value = mock_provider

        # Add an active and an inactive/error proxy in DB
        async with get_async_session() as session:
            session.add(ProxyServer(
                name="test-pm-persisted-active",
                transport="http",
                url="http://localhost",
                status="active",
                has_auth=True,
                auth_mode="bearer",
                tool_count=0
            ))
            session.add(ProxyServer(
                name="test-pm-persisted-inactive",
                transport="http",
                url="http://localhost",
                status="inactive",
                has_auth=False,
                auth_mode="none",
                tool_count=0
            ))
            await session.commit()

        await proxy_manager._ensure_plugins_row("test-pm-persisted-active")
        await vault.set("proxy_test-pm-persisted-active", "bearer", "persisted-token")

        # Load persisted
        await proxy_manager.load_persisted()

        # Check build_proxy was called with token
        mock_build.assert_any_call(
            name="test-pm-persisted-active",
            transport="http",
            url="http://localhost",
            auth_mode="bearer",
            bearer_token="persisted-token",
            oauth_config=None
        )

        # Check tool_count was updated for the active proxy
        async with get_async_session() as session:
            stmt = select(ProxyServer).where(ProxyServer.name == "test-pm-persisted-active")
            row = (await session.execute(stmt)).scalar_one()
            assert row.tool_count == 2
            assert row.status == "active"


@pytest.mark.asyncio
async def test_update_proxy_description():
    # Setup a mock proxy in the database
    async with get_async_session() as session:
        db_row = ProxyServer(
            name="test-pm-update-desc",
            transport="http",
            url="http://localhost",
            status="active",
            has_auth=False,
            auth_mode="none",
            tool_count=2,
            error_message=None,
            custom_description="Old Description"
        )
        session.add(db_row)
        await session.commit()

    # 1. Update when not mounted/active (should update DB but not trigger re-embed)
    # Since 'test-pm-update-desc' is NOT in proxy_manager._active_handles, no re-embedding happens.
    res = await proxy_manager.update_proxy_description("test-pm-update-desc", "New Description")
    assert res == {
        "name": "test-pm-update-desc",
        "customDescription": "New Description",
        "workspaceLabel": None,
        "status": "ok"
    }

    # Verify DB updated
    async with get_async_session() as session:
        stmt = select(ProxyServer).where(ProxyServer.name == "test-pm-update-desc")
        row = (await session.execute(stmt)).scalar_one()
        assert row.custom_description == "New Description"

    # 2. Update when mounted/active (should trigger re-embed delete, emit, enqueue)
    mock_provider = MagicMock()
    mock_server = AsyncMock()
    mock_server.list_tools.return_value = [MagicMock(name="t1")]
    mock_provider.server = mock_server
    proxy_manager._active_handles["test-pm-update-desc"] = mock_provider

    # Mock the DB deletes, event emit, and worker enqueue
    with patch("core.plugin_loader.plugin_registry.get_registry") as mock_get_registry, \
         patch("core_graph.worker.job_producer.enqueue_pending", AsyncMock(return_value=5)) as mock_enqueue:
        
        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry

        res = await proxy_manager.update_proxy_description("test-pm-update-desc", "Latest Description")
        
        assert res["customDescription"] == "Latest Description"
        
        # Verify event was emitted
        mock_registry.events.emit.assert_called_once()
        args, kwargs = mock_registry.events.emit.call_args
        assert args[0] == "proxy.tools_discovered"
        assert args[1]["name"] == "test-pm-update-desc"
        assert args[1]["custom_description"] == "Latest Description"

        # Verify enqueue_pending was called
        mock_enqueue.assert_called_once()

    # 3. Non-existent proxy raises ValueError
    with pytest.raises(ValueError, match="Proxy 'non-existent' not found."):
        await proxy_manager.update_proxy_description("non-existent", "No Description")

    # Cleanup the active handle and DB row
    proxy_manager._active_handles.pop("test-pm-update-desc", None)
    async with get_async_session() as session:
        from sqlalchemy import delete
        await session.execute(
            delete(ProxyServer).where(ProxyServer.name == "test-pm-update-desc")
        )
        await session.commit()


@pytest.mark.asyncio
async def test_update_proxy_workspace_label():
    try:
        async with get_async_session() as session:
            db_row = ProxyServer(
                name="test-pm-update-ws",
                transport="http",
                url="http://localhost",
                status="active",
                has_auth=False,
                auth_mode="none",
                tool_count=1,
                error_message=None,
                custom_description="Desc",
                workspace_label=None,
            )
            session.add(db_row)
            await session.commit()

        res = await proxy_manager.update_proxy_description("test-pm-update-ws", workspace_label="ws-label-123")
        assert res["workspaceLabel"] == "ws-label-123"
        assert res["customDescription"] == "Desc"

        async with get_async_session() as session:
            stmt = select(ProxyServer).where(ProxyServer.name == "test-pm-update-ws")
            row = (await session.execute(stmt)).scalar_one()
            assert row.workspace_label == "ws-label-123"
            assert row.custom_description == "Desc"
    finally:
        async with get_async_session() as session:
            from sqlalchemy import delete
            await session.execute(
                delete(ProxyServer).where(ProxyServer.name == "test-pm-update-ws")
            )
            await session.commit()


@pytest.mark.asyncio
async def test_update_proxy_description_failure():
    # Setup a mock proxy in the database
    async with get_async_session() as session:
        db_row = ProxyServer(
            name="test-pm-update-desc-fail",
            transport="http",
            url="http://localhost",
            status="active",
            has_auth=False,
            auth_mode="none",
            tool_count=2,
            error_message=None,
            custom_description="Old Description"
        )
        session.add(db_row)
        await session.commit()

    mock_provider = MagicMock()
    mock_server = AsyncMock()
    # Mock list_tools to raise an exception
    mock_server.list_tools.side_effect = Exception("Downstream error")
    mock_provider.server = mock_server
    proxy_manager._active_handles["test-pm-update-desc-fail"] = mock_provider

    try:
        res = await proxy_manager.update_proxy_description("test-pm-update-desc-fail", "New Description")
        assert res["status"] == "partial_error"
        assert res["error"] == "Failed to update proxy embeddings."
        
        # Verify DB updated regardless of re-embed failure
        async with get_async_session() as session:
            stmt = select(ProxyServer).where(ProxyServer.name == "test-pm-update-desc-fail")
            row = (await session.execute(stmt)).scalar_one()
            assert row.custom_description == "New Description"
    finally:
        # Cleanup
        proxy_manager._active_handles.pop("test-pm-update-desc-fail", None)
        async with get_async_session() as session:
            from sqlalchemy import delete
            await session.execute(
                delete(ProxyServer).where(ProxyServer.name == "test-pm-update-desc-fail")
            )
            await session.commit()


@pytest.fixture(autouse=True)
def _isolate_scope_registry():
    from core.plugin_loader.scope_registry import clear
    clear()
    yield
    clear()


@pytest.mark.asyncio
async def test_register_proxy_scopes_skips_invalid_name():
    """Boot/test paths must not register scope tokens for invalid proxy names."""
    from core.plugin_loader.scope_registry import get_plugin_scopes
    from core.proxy.proxy_manager import _register_proxy_scopes

    _register_proxy_scopes("bad name!")
    assert get_plugin_scopes("proxy_bad name!") == []


@pytest.mark.asyncio
async def test_mount_registers_proxy_scope_tokens():
    """Successful proxy mount registers plugin/group scope tokens."""
    from core.plugin_loader.scope_registry import get_plugin_scopes

    with patch("core.proxy.ssrf_safety._is_safe_url", return_value=True), \
         patch("core.proxy.proxy_manager.build_proxy") as mock_build:
        mock_provider = MagicMock()
        mock_server = AsyncMock()
        mock_server.list_tools.return_value = [MagicMock(name="tool1")]
        mock_provider.server = mock_server
        mock_build.return_value = mock_provider

        await proxy_manager.add_proxy(
            name="test-pm-scopes",
            transport="http",
            url="http://localhost:9000",
            auth_mode="none",
        )

    pid = "proxy_test-pm-scopes"
    entries = get_plugin_scopes(pid)
    tokens = {e["token"] for e in entries}
    assert tokens == {f"plugin:{pid}", f"group:{pid}:proxy"}


@pytest.mark.asyncio
async def test_disable_proxy_clears_scope_tokens():
    """Disabling a proxy removes its contributed scope tokens."""
    from core.plugin_loader.scope_registry import get_plugin_scopes

    with patch("core.proxy.ssrf_safety._is_safe_url", return_value=True), \
         patch("core.proxy.proxy_manager.build_proxy") as mock_build:
        mock_provider = MagicMock()
        mock_server = AsyncMock()
        mock_server.list_tools.return_value = []
        mock_provider.server = mock_server
        mock_build.return_value = mock_provider

        await proxy_manager.add_proxy("test-pm-scope-del", "http", "http://localhost", auth_mode="none")
        assert get_plugin_scopes("proxy_test-pm-scope-del")

        await proxy_manager.disable_proxy("test-pm-scope-del")
        assert get_plugin_scopes("proxy_test-pm-scope-del") == []


@pytest.mark.asyncio
async def test_remove_proxy_clears_scope_tokens():
    """Deleting a proxy removes its contributed scope tokens."""
    from core.plugin_loader.scope_registry import get_plugin_scopes

    with patch("core.proxy.ssrf_safety._is_safe_url", return_value=True), \
         patch("core.proxy.proxy_manager.build_proxy") as mock_build:
        mock_provider = MagicMock()
        mock_server = AsyncMock()
        mock_server.list_tools.return_value = []
        mock_provider.server = mock_server
        mock_build.return_value = mock_provider

        await proxy_manager.add_proxy("test-pm-scope-rm", "http", "http://localhost", auth_mode="none")
        assert get_plugin_scopes("proxy_test-pm-scope-rm")

        await proxy_manager.remove_proxy("test-pm-scope-rm")
        assert get_plugin_scopes("proxy_test-pm-scope-rm") == []
