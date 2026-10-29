"""Relay control-plane wire contracts (consumers, relays, audit); the hub is doubled, never started."""

from unittest.mock import AsyncMock, patch

import pytest

from plugins.cat_fleet_chat_plugin.MCPTools import relay_tools as tools

_TOOLS = "plugins.cat_fleet_chat_plugin.MCPTools.relay_tools"
_SHAPE = {"response_format": "json", "limit": 3}

CONSUMERS = [
    ("fleet_consumer_list", {"owner": "sol"}, "GET", "/api/v1/consumers", "params", {"owner": "sol"}, "read"),
    ("fleet_consumer_list", {}, "GET", "/api/v1/consumers", "params", {}, "read"),
    ("fleet_consumer_create", {"actor": "sol", "name": "sol-ci", "topics": ["ci.*.done"], "start": "beginning"}, "POST", "/api/v1/consumers", "body", {"actor": "sol", "name": "sol-ci", "topics": ["ci.*.done"], "start": "beginning"}, "write"),
    ("fleet_consumer_update", {"actor": "sol", "name": "sol-ci", "channels": [], "owner": "luna"}, "PATCH", "/api/v1/consumers/sol-ci", "body", {"actor": "sol", "channels": [], "owner": "luna"}, "write"),
    ("fleet_consumer_delete", {"actor": "sol", "name": "sol-ci"}, "DELETE", "/api/v1/consumers/sol-ci", "params", {"actor": "sol"}, "write"),
    ("fleet_consumer_ack", {"name": "sol-ci", "cursor": 42}, "POST", "/api/v1/consumers/sol-ci/ack", "body", {"cursor": 42}, "write"),
    ("fleet_consumer_seek", {"actor": "sol", "name": "sol-ci", "after_event_id": 0}, "POST", "/api/v1/consumers/sol-ci/seek", "body", {"actor": "sol", "after_event_id": 0}, "write"),
    ("fleet_consumer_seek", {"actor": "sol", "name": "sol-ci", "at": "2026-10-29T00:00:00Z"}, "POST", "/api/v1/consumers/sol-ci/seek", "body", {"actor": "sol", "at": "2026-10-29T00:00:00Z"}, "write"),
    ("fleet_audit_list", {"target_kind": "relay", "target": "main", "limit": 5}, "GET", "/api/v1/audit", "params", {"target_kind": "relay", "target": "main", "limit": 5}, "read"),
]
RELAYS = [
    ("fleet_relay_list", {}, "GET", "/api/v1/relays", "params", {}, "read"),
    ("fleet_relay_get", {"relay": "main"}, "GET", "/api/v1/relays/main", "params", {}, "read"),
    ("fleet_relay_update", {"actor": "sol", "relay": "main", "expected_version": 3, "config": {"operators": ["1"]}}, "PATCH", "/api/v1/relays/main", "body", {"actor": "sol", "expected_version": 3, "config": {"operators": ["1"]}}, "admin"),
    ("fleet_relay_update", {"actor": "sol", "relay": "main", "expected_version": 4, "enabled": False}, "PATCH", "/api/v1/relays/main", "body", {"actor": "sol", "expected_version": 4, "enabled": False}, "admin"),
    ("fleet_relay_bind", {"actor": "sol", "relay": "main", "external_id": "123", "hub_channel": "fleet", "allowed": ["ops"]}, "POST", "/api/v1/relays/main/bindings", "body", {"actor": "sol", "external_id": "123", "hub_channel": "fleet", "allowed": ["ops"]}, "admin"),
    ("fleet_relay_unbind", {"actor": "sol", "relay": "main", "binding_id": 7}, "DELETE", "/api/v1/relays/main/bindings/7", "params", {"actor": "sol"}, "admin"),
    ("fleet_relay_route_add", {"actor": "sol", "relay": "main", "external_id": "123", "topics": ["ci.>"], "format": "card"}, "POST", "/api/v1/relays/main/routes", "body", {"actor": "sol", "external_id": "123", "topics": ["ci.>"], "format": "card"}, "admin"),
    ("fleet_relay_route_update", {"actor": "sol", "relay": "main", "route_id": 2, "enabled": False, "topics": []}, "PATCH", "/api/v1/relays/main/routes/2", "body", {"actor": "sol", "enabled": False, "topics": []}, "admin"),
    ("fleet_relay_route_remove", {"actor": "sol", "relay": "main", "route_id": 2}, "DELETE", "/api/v1/relays/main/routes/2", "params", {"actor": "sol"}, "admin"),
]
ALL_CASES = CONSUMERS + RELAYS


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case[0])
async def test_wire_contracts(case):
    name, args, method, path, key, payload, _tag = case
    response = {"rows": [{"id": 7}]}
    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value=response) as hub:
        result = await getattr(tools, name)(**args, _response_shape=_SHAPE)
    assert result is response
    hub.assert_awaited_once()
    assert hub.await_args.args == (method, path)
    assert (hub.await_args.kwargs.get(key) or {}) == payload
    assert hub.await_args.kwargs.get("params" if key == "body" else "body") is None
    assert hub.await_args.kwargs["tool_name"] == name
    assert hub.await_args.kwargs["shape"] == _SHAPE


@pytest.mark.asyncio
async def test_consumer_wait_clamps_and_extends_http_timeout():
    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock, return_value={}) as hub:
        await tools.fleet_consumer_wait("sol-ci", timeout_seconds=300, limit=10)
    assert hub.await_args.args == ("GET", "/api/v1/consumers/sol-ci/wait")
    assert hub.await_args.kwargs["params"] == {"timeout": 45, "limit": 10}
    assert hub.await_args.kwargs["timeout"] == 55


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", [
    ("fleet_consumer_wait", {"name": "../relays"}),
    ("fleet_consumer_ack", {"name": "x?cursor=0", "cursor": 1}),
    ("fleet_consumer_update", {"actor": "sol", "name": "a/b"}),
    ("fleet_consumer_delete", {"actor": "sol", "name": ""}),
    ("fleet_consumer_seek", {"actor": "sol", "name": "../audit", "after_event_id": 0}),
    ("fleet_relay_get", {"relay": "Main"}),
    ("fleet_relay_update", {"actor": "sol", "relay": "../consumers", "expected_version": 1}),
    ("fleet_relay_bind", {"actor": "sol", "relay": "main/x", "external_id": "1", "hub_channel": "fleet"}),
    ("fleet_relay_route_remove", {"actor": "sol", "relay": "main?x=1", "route_id": 1}),
])
async def test_bad_path_segments_fail_fast(name, args):
    with patch(f"{_TOOLS}.request_json", new_callable=AsyncMock) as hub:
        result = await getattr(tools, name)(**args)
    assert result["error"] == "validation_error"
    hub.assert_not_awaited()


def test_registration_manifest_tags_scopes_and_denylist():
    import json
    from pathlib import Path

    from core.context import mcp
    from core.proxy_tools.fastmcp_adapter import iter_tools, tool_name, tool_tags
    from db_layer.embeddings.embeddings_routes import GOAP_CANDIDATE_DENYLIST

    found = {tool_name(tool): set(tool_tags(tool)) for tool in iter_tools(mcp)}
    manifest = json.loads((Path(__file__).parents[1] / "manifest.json").read_text())
    for name, _args, _method, _path, _key, _payload, tag in ALL_CASES:
        assert name in manifest["capabilities"]
        assert found[name] == {"cat_fleet_chat_plugin", tag}
        if tag == "admin":
            assert ("cat_fleet_chat_plugin", f"cat_fleet_chat_plugin__{name}") in GOAP_CANDIDATE_DENYLIST
    assert found["fleet_consumer_wait"] == {"cat_fleet_chat_plugin", "wait"}
    assert ("cat_fleet_chat_plugin", "cat_fleet_chat_plugin__fleet_consumer_wait") in GOAP_CANDIDATE_DENYLIST
    assert "fleet_consumer_wait" in manifest["capabilities"]
    tokens = {scope["token"] for scope in manifest["scopes"]}
    assert "group:cat_fleet_chat_plugin:admin" in tokens


@pytest.mark.asyncio
async def test_relay_update_reaches_fake_hub_with_expected_version(monkeypatch):
    """Exercise the real HTTP boundary: path, body and the bearer header."""
    import json

    import httpx

    from plugins.cat_fleet_chat_plugin import hub_client

    monkeypatch.setenv("CAT_FLEET_HUB_TOKEN", "synthetic-test-token")
    seen = []

    def fake_hub(request):
        seen.append(request)
        assert request.method == "PATCH"
        assert request.url.path == "/api/v1/relays/main"
        assert json.loads(request.content) == {"actor": "sol", "expected_version": 3, "enabled": False}
        assert request.headers["Authorization"] == "Bearer synthetic-test-token"
        return httpx.Response(412, json={"error": {"code": "version_conflict", "message": "relay main changed", "details": {"version": 4}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_hub)) as client:
        with (
            patch.object(hub_client, "open_client", new_callable=AsyncMock, return_value=client),
            patch.object(hub_client, "hub_url", return_value="https://hub.example.test"),
            patch.object(hub_client, "_is_safe_url", new_callable=AsyncMock, return_value=True),
        ):
            result = await tools.fleet_relay_update("sol", "main", 3, enabled=False)
    assert len(seen) == 1
    assert result == {
        "status": "error",
        "error": "version_conflict",
        "message": "relay main changed",
        "http_status": 412,
        "details": {"version": 4},
    }
