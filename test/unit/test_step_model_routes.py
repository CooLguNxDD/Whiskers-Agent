"""Unit tests for step model policy config routes in api/config_routes.py."""

from unittest.mock import AsyncMock

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from api.config_routes import get_step_models, save_step_models

routes = [
    Route("/api/config/session_gated/step-models", get_step_models, methods=["GET"]),
    Route("/api/config/session_gated/step-models", save_step_models, methods=["POST"]),
]

app = Starlette(routes=routes)

SAMPLE_POLICY = {
    "strategy": "strength",
    "task_type_map": {},
    "op_overrides": {},
    "parallel_enabled": True,
    "fanout_concurrency": 5,
}


def test_get_step_models(monkeypatch):
    mock_get_policy = AsyncMock(return_value=dict(SAMPLE_POLICY))
    mock_list_pool = AsyncMock(
        return_value=[
            {"name": "fast-model", "is_active": True},
            {"name": "slow-model", "is_active": False},
        ]
    )

    monkeypatch.setattr(
        "db_layer.step_model_settings_store.get_step_model_policy",
        mock_get_policy,
    )
    monkeypatch.setattr("core.llm_config_service.list_pool", mock_list_pool)

    client = TestClient(app)
    response = client.get("/api/config/session_gated/step-models")
    assert response.status_code == 200
    data = response.json()
    assert "policy" in data
    assert data["policy"]["strategy"] == "strength"
    assert data["pool_names"] == ["fast-model"]


def test_save_step_models_invalid_json():
    client = TestClient(app)
    response = client.post("/api/config/session_gated/step-models", content="not json")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"


def test_save_step_models_invalid_json_type():
    client = TestClient(app)
    response = client.post("/api/config/session_gated/step-models", json=[1, 2, 3])
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"


def test_save_step_models_success(monkeypatch):
    merged_policy = {**SAMPLE_POLICY, "strategy": "task_type"}
    mock_set_policy = AsyncMock(return_value=merged_policy)

    monkeypatch.setattr(
        "db_layer.step_model_settings_store.set_step_model_policy",
        mock_set_policy,
    )
    monkeypatch.setattr("core_graph.mcp_tool.invalidate_graph", lambda: None)

    client = TestClient(app)
    response = client.post("/api/config/session_gated/step-models", json={"strategy": "task_type"})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["policy"]["strategy"] == "task_type"