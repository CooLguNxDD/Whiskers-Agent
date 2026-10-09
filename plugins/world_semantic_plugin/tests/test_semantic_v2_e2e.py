"""End-to-end tests for world_semantic_v2 semantic tools and Unity bridge routes.

Exercises real registered adapters, service layers, and queue boundaries with
deterministic mocked model/provider and persistence I/O (no network, DB, GPU, Unity).
"""
from __future__ import annotations

import base64
import copy
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.interfaces.principal import Principal
from core.route_registry.operation_catalog import get_operation_catalog
from core.scope_management import (
    PrincipalKind,
    ScopeGrant,
    evaluate_access,
)
from plugins.world_semantic_plugin.hexmath import (
    Projection,
    cell,
    cell_center_meters,
)
from plugins.world_semantic_plugin.world_documents import AuthorizedWorld

# Test constants
TENANT_ID = 7
WORLD_ID = "demo"
PROJ = Projection(anchor_lat=12.0, anchor_lng=25.0, base_res=10)
HEX_ID = cell(450.0, 700.0, PROJ)
CENTER_POS = list(cell_center_meters(HEX_ID, PROJ))
SEL_ASSET = {"provider": "gemma-multimodal", "model": "test-asset-model", "dimensions": 3}
SEL_WORLD = {"provider": "gemma-multimodal", "model": "test-world-model", "dimensions": 3}


def make_png(color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buf, format="PNG")
    return buf.getvalue()


def make_http_request(
    method: str,
    path: str,
    body: bytes,
    headers: list[tuple[bytes, bytes]] | None = None,
    path_params: dict[str, str] | None = None,
) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [(b"authorization", b"Bearer octk_test_token")],
        "path_params": path_params or {"world_id": WORLD_ID},
    }
    return Request(scope, receive)


class MockDatabase:
    """In-memory transactional SQL simulation for assets, world hexes, and jobs."""

    def __init__(self):
        self.assets = {}  # (tenant_id, world_id, asset_id) -> record
        self.world_vectors = {}  # (namespace, doc_kind, doc_id, model) -> record
        self.jobs = {}  # content_hash -> job_dict
        self.job_counter = 100
        self.worlds = {
            WORLD_ID: {
                "world_id": WORLD_ID,
                "name": WORLD_ID,
                "anchor_lat": PROJ.anchor_lat,
                "anchor_lng": PROJ.anchor_lng,
                "meters_per_degree": PROJ.meters_per_degree,
                "base_res": PROJ.base_res,
                "layer_height": 3.0,
            }
        }

    def get_world(self, world_id: str) -> dict | None:
        return self.worlds.get(world_id)


@pytest.fixture
def mock_db():
    return MockDatabase()


@pytest.mark.asyncio
async def test_asset_ingest_and_search_e2e(tmp_path, mock_db, monkeypatch):
    """1. Asset ingest via exposed surface, queued processing, then search with filters; unchanged re-ingest."""
    from plugins.world_semantic_plugin import asset_adapters, asset_index
    from plugins.world_semantic_plugin.MCPTools import semantic_tools
    from plugins.world_semantic_plugin.stores import asset_store

    # Set up trusted root for references
    trusted_dir = tmp_path / "trusted_assets"
    trusted_dir.mkdir(parents=True, exist_ok=True)
    img_bytes = make_png("green")
    (trusted_dir / "bridge.png").write_bytes(img_bytes)

    # Configure server settings for trusted roots
    monkeypatch.setattr(
        asset_adapters,
        "_trusted_root",
        lambda t, w: trusted_dir,
    )

    # Principal with write + read access
    principal = Principal(
        subject="test_agent",
        scopes=frozenset({"group:world_semantic_plugin:read", "group:world_semantic_plugin:write"}),
        tenant_id=TENANT_ID,
    )
    auth_service_mock = SimpleNamespace(
        principal_from_bearer=AsyncMock(return_value=principal)
    )
    monkeypatch.setattr(asset_adapters, "get_auth_service", lambda: auth_service_mock)
    monkeypatch.setattr("core.auth_service.get_auth_service", lambda: auth_service_mock)
    monkeypatch.setattr(asset_adapters, "principal_for_tool", AsyncMock(return_value=principal))

    # Mock embeddings resolution
    monkeypatch.setattr(asset_index, "resolve_asset_embedding", AsyncMock(return_value=SEL_ASSET))

    # Wire storage functions to mock_db
    staged_jobs = []

    async def fake_stage_assets(records, tenant_id, world_id, model_id, dimensions):
        enqueued = 0
        indexed = 0
        for rec in records:
            key = (tenant_id, world_id, rec["asset_id"])
            existing = mock_db.assets.get(key)
            if existing and existing.get("content_hash") == rec["content_hash"] and existing.get("status") == "indexed":
                indexed += 1
                continue

            mock_db.assets[key] = {
                "tenant_id": tenant_id,
                "world_id": world_id,
                "asset_id": rec["asset_id"],
                "content_hash": rec["content_hash"],
                "content_text": rec["content_text"],
                "meta": rec["meta"],
                "status": "enqueued",
                "embedding": None,
                "dimensions": dimensions,
            }
            job_dict = {
                "id": mock_db.job_counter,
                "plugin_id": asset_store.PLUGIN_ID,
                "operation_id": asset_store.OPERATION_ID,
                "content_hash": rec["content_hash"],
                "payload": {
                    "tenant_id": tenant_id,
                    "world_id": world_id,
                    "asset_id": rec["asset_id"],
                    "model": model_id,
                    "dimensions": dimensions,
                    "content_text": rec["content_text"],
                    "content_hash": rec["content_hash"],
                    "generation": "gen-1",
                    "documents": [
                        {"text": rec["content_text"], "media": None}
                    ],
                },
                "status": "pending",
            }
            mock_db.job_counter += 1
            mock_db.jobs[rec["content_hash"]] = job_dict
            staged_jobs.append(job_dict)
            enqueued += 1

        status = "indexed" if enqueued == 0 else "enqueued"
        return {"status": status, "enqueued": enqueued, "pending": 0, "indexed": indexed, "warnings": []}

    monkeypatch.setattr(asset_store, "stage_assets", fake_stage_assets)

    # 1. Ingest via HTTP adapter
    asset_doc = {
        "id": "stone_bridge_01",
        "kind": "prop",
        "description": "Weathered stone arch bridge crossing a clear river",
        "tags": ["stone", "river", "crossing"],
        "craftRoles": ["bridge"],
        "bounds": [10.0, 4.0, 5.0],
        "variants": [{"name": "default", "path": "Assets/bridge.glb", "sha256": "f" * 64}],
        "defaultVariant": "default",
        "reference": {"image": "bridge.png", "audio": None},
    }
    index_payload = {"version": 1, "assets": [asset_doc]}

    http_req = make_http_request("POST", f"/api/world/none/{WORLD_ID}/assets/index", json.dumps(index_payload).encode())
    resp = await asset_adapters.index_assets_http(http_req)
    assert resp.status_code == 202
    body = json.loads(resp.body.decode())
    assert body["status"] == "enqueued"
    assert body["enqueued"] == 1

    # Verify asset is enqueued but not yet searchable
    assert mock_db.assets[(TENANT_ID, WORLD_ID, "stone_bridge_01")]["status"] == "enqueued"

    # 2. Worker processing (mock provider embedding vectors)
    async def fake_embed_multimodal(sel, docs):
        return [[0.2, 0.4, 0.8] for _ in docs]

    monkeypatch.setattr("db_layer.embeddings.embeddings_core.embed_multimodal_with", fake_embed_multimodal)

    # Process queued jobs
    for j in staged_jobs:
        key = (j["payload"]["tenant_id"], j["payload"]["world_id"], j["payload"]["asset_id"])
        mock_db.assets[key]["status"] = "indexed"
        mock_db.assets[key]["embedding"] = [0.2, 0.4, 0.8]
        j["status"] = "done"

    # 3. Search via MCP callable and HTTP adapter with filters
    async def fake_search_by_vector(spec, query_vec, k, model_id=None, extra_filters=None, row_filter=None):
        hits = []
        for (t, w, a_id), rec in mock_db.assets.items():
            if t == TENANT_ID and w == WORLD_ID and rec["status"] == "indexed":
                hits.append({
                    "asset_id": a_id,
                    "metadata": {
                        "kind": rec["meta"]["kind"],
                        "tags": rec["meta"]["tags"],
                        "description": rec["content_text"],
                    },
                    "score": 0.92,
                })
        return hits[:k]

    monkeypatch.setattr(asset_index, "search_by_vector", fake_search_by_vector)
    monkeypatch.setattr(
        "db_layer.embeddings.embeddings_core.embed_multimodal_with",
        AsyncMock(return_value=[[0.2, 0.4, 0.8]]),
    )

    # Search via MCP tool
    mcp_hits = await semantic_tools.search_assets(
        world_id=WORLD_ID,
        query="bridge over river",
        kind="prop",
        tags=["stone"],
        k=5,
    )
    assert len(mcp_hits) == 1
    assert mcp_hits[0]["asset_id"] == "stone_bridge_01"
    assert mcp_hits[0]["score"] == 0.92

    # Search via HTTP adapter
    search_req = make_http_request(
        "POST",
        f"/api/world/none/{WORLD_ID}/assets/search",
        json.dumps({"query": "stone bridge", "kind": "prop", "k": 5}).encode(),
    )
    search_resp = await asset_adapters.search_assets_http(search_req)
    assert search_resp.status_code == 200
    search_data = json.loads(search_resp.body.decode())
    assert search_data["status"] == "ok"
    assert len(search_data["assets"]) == 1
    assert search_data["assets"][0]["asset_id"] == "stone_bridge_01"

    # 4. Unchanged re-ingest: verify idempotent no-op reporting indexed
    reingest_req = make_http_request("POST", f"/api/world/none/{WORLD_ID}/assets/index", json.dumps(index_payload).encode())
    reingest_resp = await asset_adapters.index_assets_http(reingest_req)
    assert reingest_resp.status_code == 200
    reingest_data = json.loads(reingest_resp.body.decode())
    assert reingest_data["status"] == "indexed"
    assert reingest_data["enqueued"] == 0
    assert reingest_data["indexed"] == 1


@pytest.mark.asyncio
async def test_world_inspect_snapshot_and_search_e2e(mock_db, monkeypatch):
    """2. World inspect/snapshot ingest, queued processing, then search returning hex identity, center, frame."""
    from plugins.world_semantic_plugin import world_adapters, world_index
    from plugins.world_semantic_plugin.MCPTools import semantic_tools
    from plugins.world_semantic_plugin.stores import world_hex_store

    # Established world in mock_db
    monkeypatch.setattr(world_adapters, "get_world", AsyncMock(return_value=mock_db.get_world(WORLD_ID)))
    monkeypatch.setattr(world_hex_store, "resolve_unity_world_embedding", AsyncMock(return_value=SEL_WORLD))
    monkeypatch.setattr(world_index, "resolve_unity_world_embedding", AsyncMock(return_value=SEL_WORLD))

    principal = Principal(
        subject="world_builder",
        scopes=frozenset({"group:world_semantic_plugin:read", "group:world_semantic_plugin:write"}),
        tenant_id=TENANT_ID,
    )
    auth_service_mock = SimpleNamespace(
        principal_from_bearer=AsyncMock(return_value=principal)
    )
    monkeypatch.setattr(world_adapters, "get_auth_service", lambda: auth_service_mock)
    monkeypatch.setattr("core.auth_service.get_auth_service", lambda: auth_service_mock)
    monkeypatch.setattr(world_adapters, "principal_for_tool", AsyncMock(return_value=principal))

    # Mock reservation/storage in world_hex_store
    staged_jobs = []

    async def fake_reserve_document(doc):
        key = (doc.context.namespace, world_hex_store.DOC_KIND, doc.hex_id, doc.model_id)
        mock_db.world_vectors[key] = {
            "world_id": doc.context.namespace,
            "doc_kind": world_hex_store.DOC_KIND,
            "doc_id": doc.hex_id,
            "model": doc.model_id,
            "content_hash": doc.content_hash,
            "content_text": doc.summary,
            "meta": {"revision": doc.revision},
            "embedding": None,
        }
        job = {
            "id": mock_db.job_counter,
            "plugin_id": "world_semantic_plugin",
            "operation_id": world_hex_store.OPERATION_ID,
            "content_hash": doc.content_hash,
            "payload": doc.job_payload(),
        }
        mock_db.job_counter += 1
        staged_jobs.append(job)
        return "queued"

    monkeypatch.setattr(world_hex_store, "reserve_document", fake_reserve_document)

    # Inspect + snapshot envelope
    img_b64 = base64.b64encode(make_png("blue")).decode("ascii")
    envelope = {
        "world_id": WORLD_ID,
        "revision": 1,
        "inspect": {
            "success": True,
            "message": "world_inspect",
            "data": {
                "ok": True,
                "center": CENTER_POS,
                "radius": 1.0,
                "histogram": {"grass": 10, "water": 5},
                "height": {"min": 1.0, "max": 4.0, "avg": 2.5},
                "slope": {"0-5": 2},
                "waterPercent": 30,
                "waterLevel": 2.0,
                "propHistogram": {"willow_tree": 2},
                "waterBodies": [{"id": "serene_lake", "level": 2.0, "flow": [0.0, 0.0]}],
                "splines": [{"id": "trail", "kind": "footpath", "points": [CENTER_POS, [CENTER_POS[0] + 1, CENTER_POS[1] + 1]]}],
            },
        },
        "snapshot_center": CENTER_POS,
        "snapshot": {
            "success": True,
            "data": {
                "ok": True,
                "base64": img_b64,
                "width": 2,
                "height": 2,
                "sizeX": 2,
                "sizeZ": 2,
                "view": "top",
            },
        },
    }

    # 1. Ingest via HTTP adapter
    http_req = make_http_request("POST", f"/api/world/none/{WORLD_ID}/world/index", json.dumps(envelope).encode())
    resp = await world_adapters.index_world_http(http_req)
    assert resp.status_code == 202
    resp_body = json.loads(resp.body.decode())
    assert resp_body["status"] == "queued"
    assert resp_body["hex_id"] == HEX_ID
    assert resp_body["revision"] == 1

    # Verify reserved in store
    ns = f'world-hex-v1:[{TENANT_ID},"{WORLD_ID}"]'
    vec_key = (ns, world_hex_store.DOC_KIND, HEX_ID, "gemma-multimodal:test-world-model:3")
    assert vec_key in mock_db.world_vectors
    assert mock_db.world_vectors[vec_key]["embedding"] is None

    # 2. Worker processing
    for j in staged_jobs:
        mock_db.world_vectors[vec_key]["embedding"] = [0.1, 0.5, 0.9]

    # 3. Search via MCP and HTTP adapters
    async def fake_search_documents(context, query, selection, k):
        assert context.namespace == ns
        assert context.world_id == WORLD_ID
        assert context.tenant_id == TENANT_ID
        return [{
            "doc_id": HEX_ID,
            "content_text": mock_db.world_vectors[vec_key]["content_text"],
            "score": 0.88,
        }]

    monkeypatch.setattr(world_hex_store, "search_documents", fake_search_documents)

    # Search via MCP tool
    mcp_results = await semantic_tools.search_world(world_id=WORLD_ID, query="serene lake with trees", k=5)
    assert len(mcp_results) == 1
    hit = mcp_results[0]
    assert hit["hex_id"] == HEX_ID
    assert hit["center_pos"] == CENTER_POS
    assert hit["center_frame"] == "unity_xz_meters"
    assert hit["score"] == 0.88
    assert "serene_lake" in hit["summary"]

    # Search via HTTP adapter
    search_req = make_http_request(
        "POST",
        f"/api/world/none/{WORLD_ID}/world/search",
        json.dumps({"query": "lake near willow", "k": 5}).encode(),
    )
    search_resp = await world_adapters.search_world_http(search_req)
    assert search_resp.status_code == 200
    search_data = json.loads(search_resp.body.decode())
    assert search_data["status"] == "ok"
    assert len(search_data["results"]) == 1
    assert search_data["results"][0]["hex_id"] == HEX_ID
    assert search_data["results"][0]["center_frame"] == "unity_xz_meters"


@pytest.mark.asyncio
async def test_registration_auth_and_validation_e2e(mock_db, monkeypatch):
    """3. Registration, scope wiring, auth failures, spoofed tenant/world/root rejection across HTTP and MCP."""
    from plugins.world_semantic_plugin import asset_adapters, world_adapters
    from plugins.world_semantic_plugin.MCPTools import semantic_tools
    from plugins.world_semantic_plugin.routes import register_routes
    from core.context import http_route_registry

    # 1. Operation registration and discovery
    register_routes()
    route_paths = [d.path for d in http_route_registry._all_decls if d.owner == "world_semantic_plugin"]

    assert "/api/world/none/{world_id}/assets/search" in route_paths
    assert "/api/world/none/{world_id}/assets/index" in route_paths
    assert any("search" in p and "world" in p for p in route_paths)
    assert any("index" in p and "world" in p for p in route_paths)

    # FastMCP tools discoverable
    from core.proxy_tools.static_tool_loader import collect_from
    descriptors = collect_from("world_semantic_plugin", ["plugins.world_semantic_plugin.MCPTools"])
    tool_map = {d.operation_id: d for d in descriptors}

    assert "world_semantic_plugin__search_assets" in tool_map
    assert "world_semantic_plugin__search_world" in tool_map
    assert "world_semantic_plugin__index_assets" in tool_map
    assert "world_semantic_plugin__index_world" in tool_map

    # Check read/write tagging
    assert tool_map["world_semantic_plugin__search_assets"].access == "read"
    assert tool_map["world_semantic_plugin__search_world"].access == "read"
    assert tool_map["world_semantic_plugin__index_assets"].access == "write"
    assert tool_map["world_semantic_plugin__index_world"].access == "write"

    # 2. Unauthenticated calls -> 401 on HTTP, PermissionError on MCP
    unauthed_auth = SimpleNamespace(principal_from_bearer=AsyncMock(return_value=None))
    monkeypatch.setattr(asset_adapters, "get_auth_service", lambda: unauthed_auth)
    monkeypatch.setattr(world_adapters, "get_auth_service", lambda: unauthed_auth)
    monkeypatch.setattr("core.auth_service.get_auth_service", lambda: unauthed_auth)
    monkeypatch.setattr(asset_adapters, "principal_for_tool", AsyncMock(return_value=None))
    monkeypatch.setattr(world_adapters, "principal_for_tool", AsyncMock(return_value=None))

    with pytest.raises(PermissionError):
        await semantic_tools.search_assets(WORLD_ID, query="stone")
    with pytest.raises(PermissionError):
        await semantic_tools.search_world(WORLD_ID, query="lake")
    with pytest.raises(PermissionError):
        await semantic_tools.index_assets(WORLD_ID, {})
    with pytest.raises(PermissionError):
        await semantic_tools.index_world(WORLD_ID, {})

    no_auth_req = make_http_request("POST", f"/api/world/none/{WORLD_ID}/assets/search", b"{}", headers=[])
    assert (await asset_adapters.search_assets_http(no_auth_req)).status_code == 401

    no_auth_world_req = make_http_request("POST", f"/api/world/none/{WORLD_ID}/world/search", b"{}", headers=[])
    assert (await world_adapters.search_world_http(no_auth_world_req)).status_code == 401

    # 3. Missing explicit tenant -> 403 on HTTP, PermissionError on MCP
    tenantless_principal = Principal("user", frozenset({"group:world_semantic_plugin:read"}), tenant_id=None)
    tenantless_auth = SimpleNamespace(principal_from_bearer=AsyncMock(return_value=tenantless_principal))
    monkeypatch.setattr(asset_adapters, "get_auth_service", lambda: tenantless_auth)
    monkeypatch.setattr(world_adapters, "get_auth_service", lambda: tenantless_auth)
    monkeypatch.setattr("core.auth_service.get_auth_service", lambda: tenantless_auth)
    monkeypatch.setattr(asset_adapters, "principal_for_tool", AsyncMock(return_value=tenantless_principal))
    monkeypatch.setattr(world_adapters, "principal_for_tool", AsyncMock(return_value=tenantless_principal))

    with pytest.raises(PermissionError, match="explicit tenant"):
        await semantic_tools.search_assets(WORLD_ID, query="stone")
    with pytest.raises(PermissionError, match="explicit tenant"):
        await semantic_tools.search_world(WORLD_ID, query="lake")

    tenantless_req = make_http_request("POST", f"/api/world/none/{WORLD_ID}/assets/search", b'{"query":"stone"}')
    assert (await asset_adapters.search_assets_http(tenantless_req)).status_code == 403
    tenantless_world_req = make_http_request("POST", f"/api/world/none/{WORLD_ID}/world/search", b'{"query":"lake"}')
    assert (await world_adapters.search_world_http(tenantless_world_req)).status_code == 403

    # 4. Scope mismatch: read-scoped principal attempting write operations
    read_only_principal = Principal(
        "reader",
        frozenset({"group:world_semantic_plugin:read"}),
        tenant_id=TENANT_ID,
    )
    read_auth = SimpleNamespace(principal_from_bearer=AsyncMock(return_value=read_only_principal))
    monkeypatch.setattr(asset_adapters, "get_auth_service", lambda: read_auth)
    monkeypatch.setattr(world_adapters, "get_auth_service", lambda: read_auth)
    monkeypatch.setattr("core.auth_service.get_auth_service", lambda: read_auth)
    monkeypatch.setattr(asset_adapters, "principal_for_tool", AsyncMock(return_value=read_only_principal))
    monkeypatch.setattr(world_adapters, "principal_for_tool", AsyncMock(return_value=read_only_principal))

    with pytest.raises(PermissionError, match="scope denied"):
        await semantic_tools.index_assets(WORLD_ID, {"assets": []})
    with pytest.raises(PermissionError, match="scope denied"):
        await semantic_tools.index_world(WORLD_ID, {})

    write_req = make_http_request("POST", f"/api/world/none/{WORLD_ID}/assets/index", b'{"version":1,"assets":[]}')
    assert (await asset_adapters.index_assets_http(write_req)).status_code == 403

    world_write_req = make_http_request("POST", f"/api/world/none/{WORLD_ID}/world/index", b'{}')
    assert (await world_adapters.index_world_http(world_write_req)).status_code == 403

    # 5. Full principal for spoofing and validation rejection checks
    full_principal = Principal(
        "admin_user",
        frozenset({"plugin:world_semantic_plugin"}),
        tenant_id=TENANT_ID,
    )
    full_auth = SimpleNamespace(principal_from_bearer=AsyncMock(return_value=full_principal))
    monkeypatch.setattr(asset_adapters, "get_auth_service", lambda: full_auth)
    monkeypatch.setattr(world_adapters, "get_auth_service", lambda: full_auth)
    monkeypatch.setattr("core.auth_service.get_auth_service", lambda: full_auth)
    monkeypatch.setattr(asset_adapters, "principal_for_tool", AsyncMock(return_value=full_principal))
    monkeypatch.setattr(world_adapters, "principal_for_tool", AsyncMock(return_value=full_principal))
    monkeypatch.setattr(world_adapters, "get_world", AsyncMock(return_value=mock_db.get_world(WORLD_ID)))

    # Spoofed tenant_id in request body
    spoof_tenant_asset = make_http_request(
        "POST",
        f"/api/world/none/{WORLD_ID}/assets/search",
        json.dumps({"query": "stone", "tenant_id": 999}).encode(),
    )
    assert (await asset_adapters.search_assets_http(spoof_tenant_asset)).status_code == 400

    spoof_tenant_world = make_http_request(
        "POST",
        f"/api/world/none/{WORLD_ID}/world/search",
        json.dumps({"query": "lake", "tenant_id": 999}).encode(),
    )
    assert (await world_adapters.search_world_http(spoof_tenant_world)).status_code == 400

    # Request-selected trusted_root in request body
    root_req_asset = make_http_request(
        "POST",
        f"/api/world/none/{WORLD_ID}/assets/index",
        json.dumps({"trusted_root": "/etc/shadow"}).encode(),
    )
    assert (await asset_adapters.index_assets_http(root_req_asset)).status_code == 400

    root_req_world = make_http_request(
        "POST",
        f"/api/world/none/{WORLD_ID}/world/index",
        json.dumps({"trusted_root": "/etc/shadow"}).encode(),
    )
    assert (await world_adapters.index_world_http(root_req_world)).status_code == 400

    # Spoofed world_id (body doesn't match URL path)
    spoof_world_req = make_http_request(
        "POST",
        f"/api/world/none/{WORLD_ID}/world/index",
        json.dumps({"world_id": "other_world", "revision": 1}).encode(),
    )
    assert (await world_adapters.index_world_http(spoof_world_req)).status_code == 400

    # Unauthorized world access (world doesn't exist)
    monkeypatch.setattr(world_adapters, "get_world", AsyncMock(return_value=None))
    unauth_world_req = make_http_request(
        "POST",
        "/api/world/none/nonexistent_world/world/search",
        json.dumps({"query": "lake"}).encode(),
        path_params={"world_id": "nonexistent_world"},
    )
    assert (await world_adapters.search_world_http(unauth_world_req)).status_code == 403
