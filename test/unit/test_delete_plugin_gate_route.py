"""Regression test for api.config_routes.delete_plugin_gate.

Before the fix, `get_plugin_gate_overrides()` returns raw dict payloads
(documented contract of db_layer.plugin_gate_store), but the route passed
one straight into `_gate_spec_to_dict`, which expects a `PluginGateSpec`
dataclass and reads `.plugin_id` / `.operations.allow` attributes. Any
plugin with an actual stored override raised AttributeError before the
delete ever ran, so the Config UI's reset-to-manifest button always 500'd.
"""

from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from core.scope_management.gates import (
    PluginGateRegistry,
    _set_plugin_gate_registry,
    parse_gate_spec,
)


def _fake_request(plugin_id: str) -> Request:
    scope = {
        "type": "http",
        "method": "DELETE",
        "path": f"/api/config/plugin-gates/{plugin_id}",
        "path_params": {"plugin_id": plugin_id},
        "headers": [],
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_delete_plugin_gate_with_stored_override_succeeds():
    from api.config_routes import delete_plugin_gate

    registry = PluginGateRegistry()
    spec = parse_gate_spec("p", {"core": ["core:graph:read"]}, owner="db")
    registry.set_db_override("p", spec)
    _set_plugin_gate_registry(registry)
    try:
        raw_stored = {"p": {"core": ["core:graph:read"]}}
        with patch(
            "db_layer.plugin_gate_store.get_plugin_gate_overrides",
            AsyncMock(return_value=raw_stored),
        ), patch(
            "db_layer.plugin_gate_store.set_plugin_gate_override",
            AsyncMock(return_value=None),
        ) as mock_set, patch(
            "api.config_routes._reload_plugin_gate_overlay", AsyncMock(return_value=None)
        ):
            response = await delete_plugin_gate(_fake_request("p"))
        assert response.status_code == 200
        mock_set.assert_awaited_once_with("p", None)
    finally:
        _set_plugin_gate_registry(None)


@pytest.mark.asyncio
async def test_delete_plugin_gate_not_found():
    from api.config_routes import delete_plugin_gate

    _set_plugin_gate_registry(PluginGateRegistry())
    try:
        with patch(
            "db_layer.plugin_gate_store.get_plugin_gate_overrides",
            AsyncMock(return_value={}),
        ):
            response = await delete_plugin_gate(_fake_request("missing"))
        assert response.status_code == 404
    finally:
        _set_plugin_gate_registry(None)
