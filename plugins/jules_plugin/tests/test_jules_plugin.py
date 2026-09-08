import os
import pytest
from core.context import mcp, vault
from core.plugin_loader.plugin_registry import PluginRegistry, _set_registry
from core.plugin_loader.plugin_loader import discover_and_load_plugins_async

@pytest.mark.asyncio
async def test_jules_plugin_loading():
    existing_key = None
    has_existing = False
        
    try:
        registry = PluginRegistry(mcp, vault=vault, relay=None)
        _set_registry(registry)
        
        # Unload any previously loaded tools to ensure a clean state
        from core.dynamic_tools.loader import unload_tools
        unload_tools("jules_plugin")
        
        # Register and load JulesPlugin in isolation
        from plugins.jules_plugin.plugin_config import JulesPlugin, _manifest
        import plugins.jules_plugin
        from core.config_loader import load_plugin_config
        
        manifest_path = os.path.join(os.path.dirname(plugins.jules_plugin.__file__), "manifest.json")
        load_plugin_config(manifest_path, _manifest)
        
        # Register the plugin in the DB registry manually to satisfy FK constraints on credentials
        if vault is not None:
            from db_layer.plugin_registry_store import DBPluginRegistry
            db_registry = DBPluginRegistry()
            await db_registry.register(_manifest)
            
            # Write mock key to Vault, backing up any existing key
            if await vault.exists("jules_plugin", "JULES_API_KEY"):
                existing_key = await vault.get("jules_plugin", "JULES_API_KEY")
                has_existing = True
            await vault.set("jules_plugin", "JULES_API_KEY", "mock_key")
            
        plugin = JulesPlugin()
        registry.lifecycle.register_plugin(plugin)
        await registry.lifecycle.initialize_plugins()
        
        # Verify jules_plugin is in the registry id map
        plugin = registry.lifecycle._plugin_id_map.get("jules_plugin")
        assert plugin is not None
        assert plugin.name == "jules_plugin"
        assert plugin.version == "1.0.0"
        
        # Check if tools are registered in FastMCP
        from core.dynamic_tools.loader import get_registered_count
        # 10 OpenAPI dynamic tools + 2 server-side review_fleet tools
        assert get_registered_count("jules_plugin") == 10
        
        # Check specific tools in local_provider (strip trailing '@' if present in FastMCP 3.x)
        provider = mcp._local_provider
        tool_names = [k.split("tool:", 1)[-1].rstrip("@") for k in provider._components.keys() if k.startswith("tool:")]
        
        expected_jules_tools = {
            "juleslist_sessions",
            "julesget_session",
            "juleslist_activities",
            "julesget_activity",
            "juleslist_sources",
            "julesget_source",
            "julescreate_session",
            "julesapprove_plan",
            "julessend_message",
            "julesdelete_session",
            "julesbuild_review_fleet",
            "julesfire_review_fleet",
        }
        
        for t in expected_jules_tools:
            assert t in tool_names, f"Expected tool '{t}' not found in registered tools: {tool_names}"
            
        # Verify that calling get_auth_headers retrieves the correct API key header
        if vault is not None:
            headers = await registry.auth.get_auth_headers("jules_plugin")
            assert headers.get("x-goog-api-key") == "mock_key"
            
    finally:
        if vault is not None:
            if has_existing:
                await vault.set("jules_plugin", "JULES_API_KEY", existing_key)
            else:
                await vault.delete("jules_plugin", "JULES_API_KEY")
