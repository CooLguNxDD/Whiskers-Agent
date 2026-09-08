import pytest
from starlette.testclient import TestClient
from starlette.applications import Starlette

@pytest.fixture
def test_app():
    import core.context
    from core.context import http_route_registry
    import api.relay_routes

    routes = []
    for decl in http_route_registry._decls.values():
        if decl.name == "api_relay_status":
             routes.append(decl.to_starlette())

    app = Starlette(routes=routes)
    return app

def test_get_relay_status_configured(test_app, monkeypatch):
    import core.context
    class MockRelay:
        pass

    monkeypatch.setattr(core.context, "oauth_relay", MockRelay())

    client = TestClient(test_app)
    response = client.get("/api/relay/session_gated/status")

    assert response.status_code == 200
    assert response.json() == {"configured": True}

def test_get_relay_status_not_configured(test_app, monkeypatch):
    import core.context
    monkeypatch.setattr(core.context, "oauth_relay", None)

    client = TestClient(test_app)
    response = client.get("/api/relay/session_gated/status")

    assert response.status_code == 200
    assert response.json() == {"configured": False}
