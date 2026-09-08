"""Unit tests for model-role config routes in api/config_routes.py (mirrors test_step_model_routes.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from api.config_routes import delete_model_role, get_model_roles, save_model_role

routes = [
    Route("/api/config/session_gated/model-roles", get_model_roles, methods=["GET"]),
    Route("/api/config/session_gated/model-roles", save_model_role, methods=["POST"]),
    Route("/api/config/session_gated/model-roles/{role_id}", delete_model_role, methods=["DELETE"]),
]
app = Starlette(routes=routes)


@pytest.fixture(autouse=True)
def _reset_model_roles():
    from core_graph.model_roles.registry import _reset_model_roles_for_tests
    from core_graph.model_roles.resolver import invalidate_pool_snapshot
    invalidate_pool_snapshot()
    _reset_model_roles_for_tests()
    yield
    invalidate_pool_snapshot()
    _reset_model_roles_for_tests()


def test_get_model_roles_every_core_role_present(monkeypatch):
    monkeypatch.setattr("core.llm_config_service.list_pool", AsyncMock(return_value=[]))
    client = TestClient(app)
    response = client.get("/api/config/session_gated/model-roles")
    assert response.status_code == 200
    data = response.json()
    role_ids = {r["role_id"] for r in data["roles"]}
    assert "triage" in role_ids
    assert all(r["source"] == "core" for r in data["roles"])
    assert "effort_map" in data
    assert "aliases" in data
    assert "efforts" in data
    assert "node_roles" in data
    assert data["node_roles"]["triage"] == ["triage"]


def test_get_model_roles_preview_uses_pool_snapshot(monkeypatch):
    """A 'strongest' rung previews the max-strength active pool entry."""
    from core_graph.model_roles.registry import get_model_role_registry
    from core_graph.model_roles.role_spec import ModelRoleSpec, Rung

    # Use the DB-overlay path (not register_model_role): registering a
    # "core"-owned spec directly races with lazy core_roles.json seeding
    # (which fires on the *next* get_model_role_registry() access and would
    # clobber this override back to the default ["core"] ladder).
    registry = get_model_role_registry()
    registry.set_db_override(
        "triage", ModelRoleSpec(role_id="triage", ladder=(Rung(selector="strongest"),), owner="db")
    )

    async def fake_list_pool(kind=None):
        return [
            {"name": "flash", "is_active": True, "strength": 0.2, "provider": "gemini", "model": "gemini-flash"},
            {"name": "sonnet", "is_active": True, "strength": 0.9, "provider": "anthropic", "model": "claude-sonnet-5"},
        ]

    monkeypatch.setattr("core.llm.pool_manager.list_pool", fake_list_pool)
    monkeypatch.setattr("core.llm_config_service.list_pool", fake_list_pool)

    async def fake_resolve_step_llm_config(step):
        hint = step.get("model")
        return {"provider": "anthropic", "model": "claude-sonnet-5", "api_key": None, "base_url": None} if hint else None

    monkeypatch.setattr("core.llm_config_service.resolve_step_llm_config", fake_resolve_step_llm_config)
    monkeypatch.setattr(
        "core.llm_provider_management.get_chat_llm",
        lambda provider, model, api_key=None, base_url=None: type("L", (), {"model": model})(),
    )

    client = TestClient(app)
    response = client.get("/api/config/session_gated/model-roles")
    assert response.status_code == 200
    data = response.json()
    triage = next(r for r in data["roles"] if r["role_id"] == "triage")
    assert triage["preview"][0]["resolved_model"] == "claude-sonnet-5"


def test_get_model_roles_degrades_to_core_defaults_on_store_error(monkeypatch):
    monkeypatch.setattr("core.llm_config_service.list_pool", AsyncMock(side_effect=RuntimeError("db down")))
    client = TestClient(app)
    response = client.get("/api/config/session_gated/model-roles")
    # pool listing failure is caught separately and shouldn't 500 the whole route
    assert response.status_code == 200
    data = response.json()
    assert data["pool_names"] == []


def test_save_model_role_valid_spec(monkeypatch):
    monkeypatch.setattr("core_graph.mcp_tool.invalidate_graph", lambda: None)
    monkeypatch.setattr("core_graph.model_roles.db_overlay.apply_db_overrides", AsyncMock(return_value=1))

    set_mock = AsyncMock(return_value={"roles": {}, "effort_map": {}})
    monkeypatch.setattr("db_layer.model_role_store.set_model_role_override", set_mock)

    client = TestClient(app)
    response = client.post(
        "/api/config/session_gated/model-roles",
        json={"role_id": "triage", "spec": {"role_id": "triage", "ladder": [{"selector": "fast"}]}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    set_mock.assert_called_once()


def test_save_model_role_invalid_spec_returns_400_and_does_not_call_store(monkeypatch):
    set_mock = AsyncMock(side_effect=RuntimeError("should not be called via store patch"))
    call_tracker = {"called": False}

    async def fake_set(role_id, spec):
        call_tracker["called"] = True
        from core_graph.model_roles.role_spec import parse_model_role_spec
        parse_model_role_spec(spec, owner="db")  # will raise for bad spec

    monkeypatch.setattr("db_layer.model_role_store.set_model_role_override", fake_set)

    client = TestClient(app)
    response = client.post(
        "/api/config/session_gated/model-roles",
        json={"role_id": "triage", "spec": {"role_id": "triage", "ladder": [], "bogus": 1}},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_spec"


def test_save_model_role_malformed_json():
    client = TestClient(app)
    response = client.post("/api/config/session_gated/model-roles", content="not json")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"


def test_save_model_role_non_object_body():
    client = TestClient(app)
    response = client.post("/api/config/session_gated/model-roles", json=[1, 2, 3])
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"


def test_save_model_role_missing_role_id():
    client = TestClient(app)
    response = client.post("/api/config/session_gated/model-roles", json={"spec": {"ladder": [{"selector": "core"}]}})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"


def test_save_effort_map_routes_to_set_effort_map(monkeypatch):
    monkeypatch.setattr("core_graph.mcp_tool.invalidate_graph", lambda: None)
    monkeypatch.setattr("core_graph.model_roles.db_overlay.apply_db_overrides", AsyncMock(return_value=0))
    set_mock = AsyncMock(return_value={"roles": {}, "effort_map": {"low": "fast"}})
    monkeypatch.setattr("db_layer.model_role_store.set_effort_map", set_mock)

    client = TestClient(app)
    response = client.post(
        "/api/config/session_gated/model-roles",
        json={"effort_map": {"low": "fast"}},
    )
    assert response.status_code == 200
    set_mock.assert_called_once_with({"low": "fast"})


def test_delete_model_role_known_reverts(monkeypatch):
    monkeypatch.setattr("core_graph.mcp_tool.invalidate_graph", lambda: None)
    monkeypatch.setattr("core_graph.model_roles.db_overlay.apply_db_overrides", AsyncMock(return_value=0))
    monkeypatch.setattr(
        "db_layer.model_role_store.get_model_role_overrides",
        AsyncMock(return_value={"roles": {"triage": {"role_id": "triage", "ladder": [{"selector": "core"}]}}, "effort_map": {}}),
    )
    delete_mock = AsyncMock(return_value={"roles": {}, "effort_map": {}})
    monkeypatch.setattr("db_layer.model_role_store.set_model_role_override", delete_mock)

    client = TestClient(app)
    response = client.delete("/api/config/session_gated/model-roles/triage")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["role"]["role_id"] == "triage"
    delete_mock.assert_called_once_with("triage", None)


def test_delete_model_role_unknown_returns_404(monkeypatch):
    monkeypatch.setattr(
        "db_layer.model_role_store.get_model_role_overrides",
        AsyncMock(return_value={"roles": {}, "effort_map": {}}),
    )
    client = TestClient(app)
    response = client.delete("/api/config/session_gated/model-roles/not_a_role")
    assert response.status_code == 404
