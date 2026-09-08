import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Import resolver first so patch can find it on the parent package namespace
import core.plugin_loader.resolver

from core.plugin_loader.plugin_loader import discover_and_load_plugins_async

@pytest.mark.asyncio
@patch("core.plugin_loader.lifecycle_manager.PluginDiscoveryService.load_config")
@patch("core.plugin_loader.lifecycle_manager.PluginDependencyResolver.resolve_plan")
@patch("core.plugin_loader.resolver")
@patch("core.plugin_loader.lifecycle_manager.PluginLifecycleManager.load_one_spec", new_callable=AsyncMock)
async def test_plugin_load_isolation(mock_load_one_spec, mock_resolver, mock_resolve_plan, mock_load_config):
    # Mock registry (plain MagicMock allows dynamically initialized attributes like .config)
    registry = MagicMock()
    registry.system_tier = 1
    registry.config = MagicMock()
    
    # Mock resolved specs
    spec_a = MagicMock()
    spec_a.name = "plugin_a"
    spec_b = MagicMock()
    spec_b.name = "plugin_b"
    
    plan = MagicMock()
    plan.order = [spec_a, spec_b]
    plan.skipped = []
    
    mock_resolve_plan.return_value = plan
    mock_load_config.return_value = ["plugin_a", "plugin_b"]
    mock_resolver._normalize_plugin_name.side_effect = lambda x: x
    
    # Make _load_one_spec raise an exception on plugin_a, but succeed on plugin_b
    def side_effect(reg, spec, **kwargs):
        if spec.name == "plugin_a":
            raise RuntimeError("Simulation of bad plugin load")
        return True
        
    mock_load_one_spec.side_effect = side_effect
    
    # This should run without throwing
    await discover_and_load_plugins_async(registry)
    
    # Verify both specs were processed
    assert mock_load_one_spec.call_count == 2
