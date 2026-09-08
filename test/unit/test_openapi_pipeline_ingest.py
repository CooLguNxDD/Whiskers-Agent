"""Unit tests for generic OpenAPI ingest adapters."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from Tools.openapi_pipeline.adapters.detect import detect_source_adapter, parse_source
from Tools.openapi_pipeline.adapters.express import parse_express_source
from Tools.openapi_pipeline.adapters.fastapi import parse_fastapi_source
from Tools.openapi_pipeline.adapters.flask import parse_flask_source
from Tools.openapi_pipeline.adapters.live import fetch_live_spec
from Tools.openapi_pipeline.adapters.spec import spec_to_routes
from Tools.openapi_pipeline.build import build_spec
from Tools.openapi_pipeline.config import IngestConfig
from Tools.openapi_pipeline.ingest import ingest
from Tools.openapi_pipeline.paths import infer_tag, openapi_path, path_param_names


def test_path_params_express_and_openapi():
    assert path_param_names("/v1/orders/:orderId/items/{itemId}") == [
        "orderId",
        "itemId",
    ]
    assert openapi_path("/items/:itemId") == "/items/{itemId}"


def test_infer_tag_skips_api_and_version_prefixes():
    assert infer_tag("/api/v1/orders/{orderId}/items") == "orders"
    assert infer_tag("/v2/catalog/skus") == "catalog"
    assert infer_tag("/health") == "health"


def test_fastapi_honors_base_path(tmp_path: Path):
    (tmp_path / "main.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/widgets/{widget_id}")\ndef get_w(widget_id: int): ...\n',
        encoding="utf-8",
    )
    config = IngestConfig(base_path="/api/v1")
    routes = parse_fastapi_source(tmp_path, tmp_path, config)
    assert routes[0]["path"] == "/api/v1/widgets/{widget_id}"
    assert routes[0]["adapter"] == "fastapi"


def test_express_and_fastapi_and_flask_source(tmp_path: Path):
    (tmp_path / "server.js").write_text(
        """
        const app = express();
        app.get('/users/:id', getUser);
        router.route('/orders').get(list).post(create);
        """,
        encoding="utf-8",
    )
    (tmp_path / "api.py").write_text(
        """
        from fastapi import FastAPI, APIRouter
        app = FastAPI()
        router = APIRouter()
        @app.get("/items/{item_id}")
        def read_item(item_id: int): ...
        @router.post("/items")
        def create_item(): ...
        app.add_api_route("/ping", ping, methods=["GET"])
        """,
        encoding="utf-8",
    )
    flask_dir = tmp_path / "flask_app"
    flask_dir.mkdir()
    (flask_dir / "views.py").write_text(
        """
        @app.route("/health")
        def health(): ...
        @bp.route("/users/<int:user_id>", methods=["GET", "DELETE"])
        def user(user_id): ...
        """,
        encoding="utf-8",
    )
    config = IngestConfig()
    express = parse_express_source(tmp_path, tmp_path, config)
    fastapi = parse_fastapi_source(tmp_path, tmp_path, config)
    flask = parse_flask_source(flask_dir, tmp_path, config)

    express_paths = {(r["method"], r["path"]) for r in express}
    assert ("GET", "/users/:id") in express_paths
    assert ("GET", "/orders") in express_paths
    assert ("POST", "/orders") in express_paths

    fastapi_paths = {(r["method"], r["path"]) for r in fastapi}
    assert ("GET", "/items/{item_id}") in fastapi_paths
    assert ("POST", "/items") in fastapi_paths
    assert ("GET", "/ping") in fastapi_paths

    flask_paths = {(r["method"], r["path"]) for r in flask}
    assert ("GET", "/health") in flask_paths
    assert ("GET", "/users/{user_id}") in flask_paths
    assert ("DELETE", "/users/{user_id}") in flask_paths


def test_spec_ingest_marks_already_specified():
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Demo", "version": "1"},
        "paths": {
            "/pets/{petId}": {
                "get": {
                    "operationId": "getPet",
                    "summary": "Get a pet",
                    "parameters": [
                        {"name": "petId", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    routes = spec_to_routes(spec)
    assert len(routes) == 1
    assert routes[0]["already_specified"] is True
    assert "get" in routes[0]["openapi_path_item"]
    assert routes[0]["path"] == "/pets/{petId}"


def test_build_spec_generic_bearer_only():
    routes = [
        {
            "method": "GET",
            "path": "/v1/widgets/:widgetId",
            "operationId": "getWidget",
            "description": "Fetch widget",
            "security": ["BearerAuth"],
            "tags": [],
        }
    ]
    spec = build_spec(routes, IngestConfig(title="Widgets", server="http://localhost:9"))
    assert "/v1/widgets/{widgetId}" in spec["paths"]
    assert "get" in spec["paths"]["/v1/widgets/{widgetId}"]
    params = spec["paths"]["/v1/widgets/{widgetId}"]["get"]["parameters"]
    assert any(p["name"] == "widgetId" for p in params)
    assert list(spec["components"]["securitySchemes"]) == ["BearerAuth"]
    assert spec["tags"][0]["name"] == "widgets"


def test_ingest_from_spec_skips_enrichment(tmp_path: Path):
    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(
        json.dumps({
            "openapi": "3.0.3",
            "info": {"title": "Live API", "version": "2.0.0"},
            "paths": {
                "/status": {
                    "get": {"operationId": "getStatus", "responses": {"200": {"description": "ok"}}}
                }
            },
        }),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    summary = ingest(
        config=IngestConfig(),
        from_spec=spec_path,
        out_dir=out,
    )
    assert summary["skip_enrichment"] is True
    assert summary["routes"] == 1
    assert (out / "enriched-manifest.json").exists()
    written = json.loads((out / "openapi.json").read_text(encoding="utf-8"))
    assert written["info"]["title"] == "Live API"


def test_fetch_live_spec_probes_well_known(tmp_path: Path):
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Probed", "version": "1"},
        "paths": {"/x": {"get": {"operationId": "getX", "responses": {"200": {"description": "ok"}}}}},
    }
    payload = json.dumps(spec).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/openapi.json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        loaded, source = fetch_live_spec(url, timeout=2)
        assert loaded["info"]["title"] == "Probed"
        assert source.endswith("/openapi.json")
    finally:
        server.shutdown()


def test_detect_fastapi_over_generic_js(tmp_path: Path):
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/a')\ndef a(): ...\n",
        encoding="utf-8",
    )
    assert detect_source_adapter(tmp_path) == "fastapi"


def test_parse_source_auto_express(tmp_path: Path):
    (tmp_path / "r.js").write_text("const app = express();\napp.post('/t', h);\n", encoding="utf-8")
    routes = parse_source("auto", tmp_path, tmp_path, IngestConfig())
    assert routes[0]["adapter"] == "express"
    assert routes[0]["method"] == "POST"


def test_ingest_rejects_non_http_url():
    with pytest.raises(ValueError, match="non-http"):
        fetch_live_spec("file:///etc/passwd")
