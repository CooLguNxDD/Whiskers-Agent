"""Integration tests for plugin management REST routes.

Patch targets must use the handler submodule binding (e.g.
``api.plugin_routes.auth_logs.get_registry``), not the package re-export —
see ``api.plugin_routes.deps`` docstring.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

class TestPluginRoutes:
    @pytest.mark.asyncio
    async def test_reindex_plugin_not_found(self, client):
        # When plugin doesn't exist in registry or database
        with patch("api.plugin_routes.auth_logs.get_registry") as mock_get_registry, \
             patch("api.plugin_routes.auth_logs._db_registry.get_by_id", new_callable=AsyncMock) as mock_db_get:
            
            # Setup mock registry with no plugins
            mock_registry = MagicMock()
            mock_registry.lifecycle._plugins = []
            mock_get_registry.return_value = mock_registry
            
            # Setup mock db registry returning None
            mock_db_get.return_value = None
            
            res = await client.post("/api/plugins/session_gated/nonexistent_plugin/reindex")
            assert res.status_code == 404
            assert res.json() == {"error": "not_found", "message": "Plugin 'nonexistent_plugin' not found."}

    @pytest.mark.asyncio
    async def test_reindex_plugin_success(self, client):
        # Mocking registry containing the target plugin
        with patch("api.plugin_routes.auth_logs.get_registry") as mock_get_registry, \
             patch(
                 "api.plugin_routes.auth_logs._db_registry.delete_plugin_route_embeddings",
                 new_callable=AsyncMock,
             ) as mock_delete_emb, \
             patch(
                 "core_graph.worker.job_producer.enqueue_pending",
                 new_callable=AsyncMock,
             ) as mock_enqueue:
            
            mock_plugin = MagicMock()
            mock_plugin.name = "my_plugin"
            mock_registry = MagicMock()
            mock_registry.lifecycle._plugins = [mock_plugin]
            mock_get_registry.return_value = mock_registry
            
            mock_enqueue.return_value = 5
            
            res = await client.post("/api/plugins/session_gated/my_plugin/reindex")
            assert res.status_code == 200
            assert res.json() == {
                "status": "ok",
                "message": "Reindexed and enqueued 5 routes for plugin 'my_plugin'"
            }
            
            mock_delete_emb.assert_awaited_once_with("my_plugin")
            mock_enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_plugin_direct_credentials_non_dict_json(self, client):
        res = await client.put("/api/plugins/session_gated/my_plugin/direct-credentials", json=[1, 2, 3])
        assert res.status_code == 400
        assert res.json() == {"error": "invalid json body", "message": "Expected a JSON object"}

    @pytest.mark.asyncio
    async def test_set_plugin_direct_credentials_api_token(self, client):
        with patch("api.plugin_routes.auth_logs._db_registry.get_by_id", new_callable=AsyncMock) as mock_db_get, \
             patch("api.plugin_routes.auth_logs.vault", new_callable=MagicMock) as mock_vault, \
             patch("api.plugin_routes.auth_logs.get_registry") as mock_get_registry:
            
            # Setup DB record
            mock_db_get.return_value = MagicMock()
            
            # Setup vault mocks
            mock_vault.set = AsyncMock()
            mock_vault.delete = AsyncMock()
            
            # Setup registry mock
            mock_registry = MagicMock()
            mock_registry.auth._vault_clear_direct_token = AsyncMock()
            mock_registry.auth.clear_needs_reauth = MagicMock()
            mock_registry.auth.refresh_auth_headers = AsyncMock(return_value={"authentication": "whiskers_api_xxx"})
            mock_registry.auth.get_auth_status = MagicMock(return_value=MagicMock(value="ok"))
            mock_get_registry.return_value = mock_registry
            
            # Call the endpoint
            res = await client.put(
                "/api/plugins/session_gated/my_plugin/direct-credentials",
                json={"api_token": "whiskers_api_xxx"}
            )
            
            assert res.status_code == 200
            assert res.json() == {"status": "ok", "auth_status": "ok"}
            
            # Assert vault calls
            mock_vault.set.assert_any_call("my_plugin", "api_token", "whiskers_api_xxx")
            mock_vault.delete.assert_any_call("my_plugin", "username")
            mock_vault.delete.assert_any_call("my_plugin", "password")
            
            mock_registry.auth._vault_clear_direct_token.assert_called_once_with("my_plugin")
            mock_registry.auth.clear_needs_reauth.assert_called_once_with("my_plugin")
            mock_registry.auth.refresh_auth_headers.assert_called_once_with("my_plugin")

    @pytest.mark.asyncio
    async def test_list_plugins_with_disabled_plugin(self, client):
        from db_layer.plugin_registry_store import PluginRecord
        
        disabled_record = PluginRecord(
            id="disabled_plugin",
            display_name="Disabled Plugin",
            version="1.2.3",
            capabilities=[],
            required_credentials=[],
            external_oauth_providers=[],
            meta={"tier": "pro", "description": "This is a disabled pro plugin"},
            is_active=False
        )
        
        with patch("api.plugin_routes.registry.get_registry") as mock_get_registry, \
             patch("api.plugin_routes.registry._db_registry.get_all", new_callable=AsyncMock) as mock_db_get:
            
            mock_registry = MagicMock()
            mock_registry.lifecycle._plugins = []
            mock_registry.system_tier = 1
            mock_get_registry.return_value = mock_registry
            mock_db_get.return_value = [disabled_record]
            
            res = await client.get("/api/plugins/session_gated")
            assert res.status_code == 200
            data = res.json()
            assert "plugins" in data
            
            plugins = data["plugins"]
            assert len(plugins) == 1
            plugin_payload = plugins[0]
            assert plugin_payload["id"] == "disabled_plugin"
            assert plugin_payload["enabled"] is False
            assert plugin_payload["tier"] == "pro"
            assert plugin_payload["description"] == "This is a disabled pro plugin"
            assert plugin_payload["version"] == "1.2.3"

    @pytest.mark.asyncio
    async def test_get_plugin_with_unloaded_plugin_normalized_tier(self, client):
        from db_layer.plugin_registry_store import PluginRecord
        
        unloaded_record = PluginRecord(
            id="unloaded_plugin",
            display_name="Unloaded Plugin",
            version="2.0.0",
            capabilities=[],
            required_credentials=[],
            external_oauth_providers=[],
            meta={"tier": "admin", "description": "This is an unloaded admin plugin"},
            is_active=False
        )
        
        with patch("api.plugin_routes.registry.get_registry") as mock_get_registry, \
             patch("api.plugin_routes.registry._db_registry.get_by_id", new_callable=AsyncMock) as mock_db_get:
            
            mock_registry = MagicMock()
            mock_registry.lifecycle._plugin_id_map = {}
            mock_get_registry.return_value = mock_registry
            mock_db_get.return_value = unloaded_record
            
            res = await client.get("/api/plugins/session_gated/unloaded_plugin")
            assert res.status_code == 200
            plugin_payload = res.json()
            assert plugin_payload["id"] == "unloaded_plugin"
            assert plugin_payload["enabled"] is False
            assert plugin_payload["tier"] == "admin"
            assert plugin_payload["description"] == "This is an unloaded admin plugin"
            assert plugin_payload["version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_get_plugin_tools_authoritative_source(self, client):
        from db_layer.plugin_registry_store import PluginRecord
        from core.route_registry.route_descriptor import RouteDescriptor

        fake_record = PluginRecord(
            id="my_plugin",
            display_name="My Plugin",
            version="1.0.0",
            capabilities=["create_record"],
            required_credentials=[],
            external_oauth_providers=[],
            meta={},
            is_active=True
        )

        fake_route = RouteDescriptor(
            plugin_id="my_plugin",
            operation_id="my_plugin__get_record",
            description="Get record details",
            parameters={},
            method="GET",
            path_template="/records/{id}",
            is_fast_path=True,
            callable_ref=None,
        )

        with patch("api.plugin_routes.tools._db_registry.get_by_id", new_callable=AsyncMock) as mock_db_get, \
             patch("api.plugin_routes.tools.get_registry") as mock_get_registry, \
             patch("api.plugin_routes.tools.mcp.list_tools", new_callable=AsyncMock) as mock_list_tools, \
             patch("db_layer.tool_config_store.get_tool_states", new_callable=AsyncMock) as mock_states, \
             patch("db_layer.tool_config_store.get_tool_hidden_states", new_callable=AsyncMock) as mock_hidden, \
             patch("db_layer.tool_config_store.get_tool_embedding_models", new_callable=AsyncMock) as mock_embeddings:

            mock_db_get.return_value = fake_record

            mock_registry = MagicMock()
            mock_registry.route_registry.routes_for_plugin.return_value = [fake_route]
            mock_get_registry.return_value = mock_registry

            mock_tool = MagicMock()
            mock_tool.name = "create_record"
            mock_tool.description = "Create a new record"
            mock_list_tools.return_value = [mock_tool]

            mock_states.return_value = {"delete_record": False, "create_record": True}
            mock_hidden.return_value = {"get_record": True}
            mock_embeddings.return_value = {}

            res = await client.get("/api/plugins/session_gated/my_plugin/tools")
            assert res.status_code == 200
            data = res.json()
            assert data["plugin_id"] == "my_plugin"

            tools = data["tools"]
            names = [t["name"] for t in tools]
            assert names == ["create_record", "delete_record", "get_record"]

            create_tool = next(t for t in tools if t["name"] == "create_record")
            assert create_tool["description"] == "Create a new record"
            assert create_tool["is_enabled"] is True
            assert create_tool["is_hidden"] is False

            delete_tool = next(t for t in tools if t["name"] == "delete_record")
            assert delete_tool["description"] == ""
            assert delete_tool["is_enabled"] is False
            assert delete_tool["is_hidden"] is False

            get_tool = next(t for t in tools if t["name"] == "get_record")
            assert get_tool["description"] == "Get record details"
            assert get_tool["is_enabled"] is True
            assert get_tool["is_hidden"] is True

    @pytest.mark.asyncio
    async def test_hide_plugin_tools_authoritative_source(self, client):
        from db_layer.plugin_registry_store import PluginRecord
        from core.route_registry.route_descriptor import RouteDescriptor

        fake_record = PluginRecord(
            id="my_plugin",
            display_name="My Plugin",
            version="1.0.0",
            capabilities=[],
            required_credentials=[],
            external_oauth_providers=[],
            meta={},
            is_active=True
        )

        fake_route = RouteDescriptor(
            plugin_id="my_plugin",
            operation_id="my_plugin__get_record",
            description="Get record details",
            parameters={},
            method="GET",
            path_template="/records/{id}",
            is_fast_path=True,
            callable_ref=None,
        )

        with patch("api.plugin_routes.tools._db_registry.get_by_id", new_callable=AsyncMock) as mock_db_get, \
             patch("api.plugin_routes.tools.get_registry") as mock_get_registry, \
             patch("api.plugin_routes.tools._db_registry.update_plugin_meta", new_callable=AsyncMock) as mock_update_meta, \
             patch("api.plugin_routes.tools.tool_visibility.hide_gateway") as mock_hide_gateway, \
             patch("db_layer.gateway_settings_store.refresh_tools_summary", new_callable=AsyncMock) as mock_refresh:

            mock_db_get.return_value = fake_record

            mock_registry = MagicMock()
            mock_registry.route_registry.routes_for_plugin.return_value = [fake_route]
            mock_get_registry.return_value = mock_registry

            mock_hide_gateway.return_value = True

            res = await client.post("/api/plugins/session_gated/my_plugin/hide-tools")
            assert res.status_code == 200
            data = res.json()
            assert data["plugin_id"] == "my_plugin"
            assert data["tools_hidden"] is True
            assert data["applied"] == 1

            mock_hide_gateway.assert_called_once_with("get_record")
            mock_update_meta.assert_awaited()
            mock_refresh.assert_called_once()



