"""Offline end-to-end coverage for the S04 semantic tools and Unity bridge routes.

Drives the registered MCP callables and HTTP adapters through the real S02/S03
services, the real shared ``embedding_worker`` claim/dispatch/completion code and
the real plugin consumers. Only I/O is replaced: a transactional in-memory SQL
double (extends the S02 test double), a deterministic embedding provider, and a
search store double that evaluates the caller-supplied SQL filters + model space.
"""
from __future__ import annotations

import base64
import copy
import io
import json
import math
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import Insert, Update
from sqlalchemy.sql.elements import BinaryExpression, BindParameter
from sqlalchemy.sql.functions import Function
from sqlalchemy.sql.selectable import Select
from starlette.requests import Request

from core.interfaces.principal import Principal
from core_graph.worker import embedding_worker
from db_layer.embeddings import embeddings_core
from db_layer.embeddings.embeddings_core import model_id_for
from plugins.world_semantic_plugin import asset_adapters, asset_index, world_adapters, world_index
from plugins.world_semantic_plugin import plugin_config
from plugins.world_semantic_plugin.asset_models import WorldAssetEmbedding as Asset
from plugins.world_semantic_plugin.hexmath import Projection, cell, cell_center_meters
from plugins.world_semantic_plugin.MCPTools import semantic_tools
from plugins.world_semantic_plugin.models import UnityWorldVector
from plugins.world_semantic_plugin.stores import asset_store, world_hex_store
from plugins.world_semantic_plugin.tests.test_asset_index import Database, Result
from plugins.world_semantic_plugin.world_documents import AuthorizedWorld

TENANT, OTHER_TENANT = 7, 8
LIBRARY = "library"
WORLD, FOREIGN_WORLD, UNOWNED_WORLD, ABSENT_WORLD = "demo", "foreign", "unowned", "absent"
PROJ = Projection(anchor_lat=12.0, anchor_lng=25.0, base_res=10)
HEX = cell(450.0, 700.0, PROJ)
CENTER = list(cell_center_meters(HEX, PROJ))
SEL_ASSET = {"provider": "gemma-multimodal", "model": "asset-space", "dimensions": 3}
SEL_WORLD = {"provider": "gemma-multimodal", "model": "world-space", "dimensions": 3}
ASSET_MODEL = model_id_for(SEL_ASSET)
WORLD_MODEL = model_id_for(SEL_WORLD)
READ = "group:world_semantic_plugin:read"
WRITE = "group:world_semantic_plugin:write"
SEMANTIC_PATHS = {
    "/api/world/none/{world_id}/assets/search",
    "/api/world/none/{world_id}/assets/index",
    "/api/world/none/{world_id}/world/search",
    "/api/world/none/{world_id}/world/index",
}


# ── Deterministic provider ─────────────────────────────────────────────────

def png(color: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(out, format="PNG")
    return out.getvalue()


def text_vector(text: str) -> list[float]:
    """3-axis keyword space: [stone/bridge, water/lake/river, plant/reed/tree]."""
    t = (text or "").lower()
    return [0.01 + sum(t.count(w) for w in ("stone", "bridge")),
            0.01 + sum(t.count(w) for w in ("water", "lake", "river")),
            0.01 + sum(t.count(w) for w in ("plant", "reed", "tree", "willow"))]


def image_vector(data: bytes) -> list[float]:
    r, g, b = Image.open(io.BytesIO(data)).convert("RGB").getpixel((0, 0))
    return [r / 255 + 0.01, b / 255 + 0.01, g / 255 + 0.01]  # red≈stone, blue≈water, green≈plant


class Provider:
    """Records every embedding call; never touches network or GPU."""

    def __init__(self):
        self.calls = []

    async def multimodal(self, selection, inputs):
        self.calls.append((dict(selection), list(inputs)))
        return [image_vector(i.media.data) if i.media is not None else text_vector(i.text) for i in inputs]


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.hypot(*a) * math.hypot(*b))


# ── SQL filter evaluation for the search store double ─────────────────────

def _eval(expr, row):
    if isinstance(expr, BindParameter):
        return expr.effective_value
    if isinstance(expr, Function):
        assert expr.name == "vector_dims", expr.name
        value = _eval(list(expr.clauses)[0], row)
        return None if value is None else len(value)
    if isinstance(expr, BinaryExpression):
        left, right = _eval(expr.left, row), _eval(expr.right, row)
        op = expr.operator
        if op is operators.eq:
            return left == right
        if op is operators.json_getitem_op:
            return (left or {}).get(right)
        opstring = getattr(op, "opstring", None)
        if opstring == "->>":
            value = (left or {}).get(right)
            return None if value is None else str(value)
        if opstring == "@>":
            return set(right) <= set(left or [])
        raise AssertionError(f"unsupported operator {op!r}")
    key = getattr(expr, "key", None)
    if key is not None:
        return getattr(row, key)
    raise AssertionError(f"unsupported filter element {expr!r}")


def filtered(stmt, rows):
    """Apply every WHERE criterion the production code supplied; no criterion is ignored."""
    return [r for r in rows if all(_eval(c, r) for c in stmt._where_criteria)]


# ── Transactional in-memory store ─────────────────────────────────────────

class SemanticDatabase(Database):
    """S02 asset/job double + unity_world_vectors + tenant-owned worlds table."""

    def __init__(self):
        super().__init__()
        self.world_vectors: dict[tuple, UnityWorldVector] = {}
        self.worlds = {
            WORLD: dict(world_id=WORLD, tenant_id=TENANT, name=WORLD, anchor_lat=PROJ.anchor_lat,
                        anchor_lng=PROJ.anchor_lng, meters_per_degree=PROJ.meters_per_degree,
                        base_res=PROJ.base_res, layer_height=3.0),
            FOREIGN_WORLD: dict(world_id=FOREIGN_WORLD, tenant_id=OTHER_TENANT, name=FOREIGN_WORLD,
                                anchor_lat=PROJ.anchor_lat, anchor_lng=PROJ.anchor_lng,
                                meters_per_degree=PROJ.meters_per_degree, base_res=PROJ.base_res,
                                layer_height=3.0),
            UNOWNED_WORLD: dict(world_id=UNOWNED_WORLD, tenant_id=None, name=UNOWNED_WORLD,
                                anchor_lat=PROJ.anchor_lat, anchor_lng=PROJ.anchor_lng,
                                meters_per_degree=PROJ.meters_per_degree, base_res=PROJ.base_res,
                                layer_height=3.0),
        }
        self.lookups: list[tuple[str, int]] = []

    async def get_world_for_tenant(self, world_id, tenant_id):
        """Mirrors the store SQL: ``WHERE world_id = :w AND tenant_id = :t``."""
        self.lookups.append((world_id, tenant_id))
        row = self.worlds.get(world_id)
        return dict(row) if row and row["tenant_id"] == tenant_id else None

    def snapshot(self):
        return copy.deepcopy((self.assets, self.jobs,
                              {k: (v.content_hash, v.embedding, dict(v.meta or {})) for k, v in self.world_vectors.items()}))

    async def execute(self, stmt, parameters=None):
        table = getattr(getattr(stmt, "table", None), "name", None)
        if isinstance(stmt, Insert) and table == "unity_world_vectors":
            p = stmt.compile(dialect=postgresql.dialect()).params
            self.statements.append(("insert unity_world_vectors", p))
            key = (p["world_id"], p["doc_kind"], p["doc_id"], p["model"])
            self.world_vectors.setdefault(key, UnityWorldVector(
                world_id=p["world_id"], doc_kind=p["doc_kind"], doc_id=p["doc_id"], model=p["model"],
                content_text=p["content_text"], content_hash=p["content_hash"], meta=p["meta"], embedding=None))
            return Result()
        if isinstance(stmt, Select) and any(getattr(f, "name", None) == "unity_world_vectors"
                                             for f in stmt.get_final_froms()):
            self.statements.append(("select unity_world_vectors", None))
            rows = filtered(stmt, self.world_vectors.values())
            return SimpleNamespace(scalar_one=lambda: rows[0], all=lambda: rows)
        if isinstance(stmt, Update) and table == "unity_world_vectors":
            # Interpret the real revision-CAS completion statement against stored rows.
            p = stmt.compile(dialect=postgresql.dialect()).params
            self.statements.append(("update unity_world_vectors", p))
            row = self.world_vectors.get((p["world_id_1"], p["doc_kind_1"], p["doc_id_1"], p["model_1"]))
            if row is None or row.content_hash != p["content_hash_1"] or row.meta.get("revision") != p["param_1"]:
                return Result()
            row.embedding = p["embedding"]
            return Result(count=1)
        return await super().execute(stmt, parameters)


@pytest.fixture
def sdb(monkeypatch, tmp_path):
    database = SemanticDatabase()
    for module in (asset_store, world_hex_store, embedding_worker):
        monkeypatch.setattr(module, "get_async_session", database.session)
    monkeypatch.setattr(asset_index, "resolve_asset_embedding", AsyncMock(return_value=SEL_ASSET))
    monkeypatch.setattr(world_index, "resolve_unity_world_embedding", AsyncMock(return_value=SEL_WORLD))
    monkeypatch.setattr(world_hex_store, "resolve_unity_world_embedding", AsyncMock(return_value=SEL_WORLD))
    monkeypatch.setattr(world_adapters, "get_world_for_tenant", database.get_world_for_tenant)
    provider = Provider()
    monkeypatch.setattr(embeddings_core, "embed_multimodal_with", provider.multimodal)
    monkeypatch.setattr(world_hex_store, "embed_multimodal_with", provider.multimodal)
    database.provider = provider
    database.search_calls = []

    async def asset_search(spec, vector, top_k, model_id, extra_filters=None, row_filter=None):
        """Store double: honors the supplied model space and every SQL filter before top-k."""
        database.search_calls.append(("asset", model_id))
        stmt = extra_filters(select(Asset))
        rows = [r for r in filtered(stmt, database.assets.values())
                if r.model == model_id and r.embedding is not None]
        ranked = sorted(rows, key=lambda r: -cosine(vector, r.embedding))[:top_k]
        return [{"asset_id": r.asset_id, "metadata": r.meta, "score": cosine(vector, r.embedding)} for r in ranked]

    async def world_search(spec, query, selection, top_k, *, extra_filters):
        """Store double for the hybrid engine: model space + supplied filters + non-null vectors."""
        model = model_id_for(selection)
        database.search_calls.append(("world", model))
        vector = text_vector(query)
        stmt = extra_filters(select(UnityWorldVector))
        rows = [r for r in filtered(stmt, database.world_vectors.values())
                if r.model == model and r.embedding is not None]
        ranked = sorted(rows, key=lambda r: -cosine(vector, r.embedding))[:top_k]
        return [{"doc_id": r.doc_id, "content_text": r.content_text, "score": cosine(vector, r.embedding)} for r in ranked]

    monkeypatch.setattr(asset_index, "search_by_vector", asset_search)
    monkeypatch.setattr(world_hex_store, "_engine_search", world_search)
    asset_store.register_consumer()
    yield database
    asset_store.unregister_consumer()


def principal(tenant=TENANT, scopes=(READ, WRITE), subject="unity-editor"):
    return Principal(subject, frozenset(scopes), tenant_id=tenant)


def act_as(monkeypatch, who: Principal | None):
    """Bind both the bearer resolver (HTTP) and the MCP access-token resolver to one principal."""
    auth = SimpleNamespace(principal_from_bearer=AsyncMock(return_value=who))
    for module in (asset_adapters, world_adapters):
        monkeypatch.setattr(module, "get_auth_service", lambda auth=auth: auth)
        monkeypatch.setattr(module, "principal_for_tool", AsyncMock(return_value=who))


def http(path: str, body, world: str, *, bearer: bool = True) -> Request:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    headers = [(b"authorization", b"Bearer octk_test")] if bearer else []
    return Request({"type": "http", "method": "POST", "path": path.format(world_id=world),
                    "headers": headers, "path_params": {"world_id": world}}, receive)


def body_of(response) -> dict:
    return json.loads(response.body.decode())


def asset_row(asset_id, kind, description, tags, image=None):
    return {"id": asset_id, "kind": kind, "description": description, "tags": tags,
            "craftRoles": ["scenery"], "bounds": [4, 2, 3],
            "variants": [{"name": "default", "path": f"Assets/{asset_id}.glb", "sha256": "a" * 64}],
            "defaultVariant": "default", "reference": {"image": image, "audio": None}}


def envelope(revision=1, *, world=WORLD, water="serene_lake"):
    """Actual Unity world_inspect + snapshot envelope with revision and snapshot-center evidence."""
    return {
        "world_id": world, "revision": revision,
        "inspect": {"success": True, "message": "world_inspect", "data": {
            "ok": True, "center": CENTER, "radius": 1.0,
            "histogram": {"grass": 10, "water": 5}, "height": {"min": 1.0, "max": 4.0, "avg": 2.5},
            "slope": {"0-5": 2}, "waterPercent": 30, "waterLevel": 2.0,
            "propHistogram": {"willow_tree": 2},
            "waterBodies": [{"id": water, "level": 2.0, "flow": [0.0, 0.0]}],
            "splines": [{"id": "trail", "kind": "footpath", "points": [CENTER, [CENTER[0] + 1, CENTER[1] + 1]]}],
        }},
        "snapshot_center": CENTER,
        "snapshot": {"success": True, "data": {
            "ok": True, "base64": base64.b64encode(png("blue")).decode(), "width": 2, "height": 2,
            "sizeX": 2, "sizeZ": 2, "view": "top"}},
    }


# ── 1. Assets ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_asset_ingest_queue_worker_then_text_and_image_search_with_filters(sdb, monkeypatch, tmp_path):
    (tmp_path / "bridge.png").write_bytes(png("red"))
    (tmp_path / "reed.png").write_bytes(png("green"))
    roots = {str(TENANT): {LIBRARY: str(tmp_path), "other_lib": str(tmp_path)},
             str(OTHER_TENANT): {LIBRARY: str(tmp_path)}}
    monkeypatch.setitem(plugin_config.SETTINGS, "asset_ingestion_roots", roots)
    library = {"version": 1, "assets": [
        asset_row("bridge", "prop", "Stone bridge over a river", ["stone", "river"], "bridge.png"),
        asset_row("stone_wall", "block", "Stone wall block", ["stone"]),
        asset_row("reed_clump", "prop", "Reed plant clump at the lake edge", ["water", "plant"], "reed.png"),
    ]}

    act_as(monkeypatch, principal())
    response = await asset_adapters.index_assets_http(http(asset_adapters.INDEX_PATH, library, LIBRARY))
    assert response.status_code == 202
    assert body_of(response)["status"] == "enqueued" and body_of(response)["enqueued"] == 3
    # Cross-tenant and cross-library decoys whose text matches every query strongly.
    decoy = {"version": 1, "assets": [asset_row("bridge", "prop", "stone bridge stone bridge river", ["stone", "river"])]}
    assert (await semantic_tools.index_assets(world_id="other_lib", index=decoy))["enqueued"] == 1
    act_as(monkeypatch, principal(tenant=OTHER_TENANT))
    assert (await semantic_tools.index_assets(world_id=LIBRARY, index=decoy))["enqueued"] == 1
    act_as(monkeypatch, principal())

    # Queued, not yet searchable: desired state is stored, vectors are not.
    assert {a.status for a in sdb.assets.values()} == {"enqueued"} and len(sdb.jobs) == 5
    assert all(j.status == "pending" and j.operation_id == asset_store.OPERATION_ID for j in sdb.jobs.values())
    assert await semantic_tools.search_assets(world_id=LIBRARY, query="stone bridge") == []

    # Real shared worker: claim SQL → registered consumer → generation-guarded completion.
    await embedding_worker._drain_until_idle(10)
    assert {j.status for j in sdb.jobs.values()} == {"done"}
    assert {a.status for a in sdb.assets.values()} == {"indexed"}
    assert all(a.model == ASSET_MODEL and len(a.embedding) == 3 for a in sdb.assets.values())
    embedded_media = [i.media.mime_type for _, inputs in sdb.provider.calls for i in inputs if i.media]
    assert embedded_media == ["image/png", "image/png"]  # both reference images, read from trusted root
    # A row in a different model space with an ideal vector must never be returned.
    sdb.assets[TENANT, LIBRARY, "stale_space"] = SimpleNamespace(
        tenant_id=TENANT, world_id=LIBRARY, asset_id="stale_space", kind="prop", status="indexed",
        model="gemma-multimodal:retired:3", meta={"tags": ["stone", "river"], "kind": "prop"},
        embedding=text_vector("stone bridge river"))

    # Text search through MCP with kind + tags + k.
    hits = await semantic_tools.search_assets(world_id=LIBRARY, query="stone bridge over a river",
                                              kind="prop", tags=["stone"], k=5)
    assert [h["asset_id"] for h in hits] == ["bridge"]
    assert hits[0]["metadata"]["kind"] == "prop" and set(hits[0]["metadata"]["tags"]) == {"stone", "river"}
    assert isinstance(hits[0]["score"], float)
    # No filters: only this tenant/library/model; k truncates after ranking.
    hits = await semantic_tools.search_assets(world_id=LIBRARY, query="stone", k=2)
    assert {h["asset_id"] for h in hits} == {"stone_wall", "bridge"}  # reed ranks last, cut by k
    assert hits[0]["score"] >= hits[1]["score"]
    hits = await semantic_tools.search_assets(world_id=LIBRARY, query="stone", k=10)
    assert {h["asset_id"] for h in hits} == {"stone_wall", "bridge", "reed_clump"}
    assert await semantic_tools.search_assets(world_id=LIBRARY, query="stone", kind="audio") == []
    assert await semantic_tools.search_assets(world_id=LIBRARY, query="stone", tags=["stone", "plant"]) == []

    # Image search through HTTP: green reference ranks the reed first; filters still apply.
    image = {"mime_type": "image/png", "data": base64.b64encode(png("green")).decode()}
    response = await asset_adapters.search_assets_http(
        http(asset_adapters.SEARCH_PATH, {"image": image, "k": 1}, LIBRARY))
    assert response.status_code == 200 and [h["asset_id"] for h in body_of(response)["assets"]] == ["reed_clump"]
    response = await asset_adapters.search_assets_http(
        http(asset_adapters.SEARCH_PATH, {"image": image, "kind": "block", "tags": ["stone"]}, LIBRARY))
    assert [h["asset_id"] for h in body_of(response)["assets"]] == ["stone_wall"]
    assert {model for _, model in sdb.search_calls} == {ASSET_MODEL}

    # Other tenant sees only its own decoy under the same library name.
    act_as(monkeypatch, principal(tenant=OTHER_TENANT))
    hits = await semantic_tools.search_assets(world_id=LIBRARY, query="stone bridge", k=10)
    assert [h["asset_id"] for h in hits] == ["bridge"] and hits[0]["metadata"]["description"].startswith("stone bridge stone")
    act_as(monkeypatch, principal())

    # Unchanged re-ingest: no new job, no provider call, identity preserved.
    calls, jobs = len(sdb.provider.calls), copy.deepcopy(sdb.jobs)
    response = await asset_adapters.index_assets_http(http(asset_adapters.INDEX_PATH, library, LIBRARY))
    assert response.status_code == 200
    assert body_of(response) | {"warnings": []} == {"status": "indexed", "enqueued": 0, "pending": 0,
                                                    "indexed": 3, "warnings": []}
    assert len(sdb.provider.calls) == calls and {k: vars(v) for k, v in sdb.jobs.items()} == {k: vars(v) for k, v in jobs.items()}


# ── 2. World hexes ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_world_envelope_ingest_queue_worker_then_search(sdb, monkeypatch):
    namespace = AuthorizedWorld(TENANT, WORLD, PROJ).namespace
    act_as(monkeypatch, principal())
    response = await world_adapters.index_world_http(http(world_adapters.INDEX_WORLD_PATH, envelope(), WORLD))
    assert response.status_code == 202
    queued = body_of(response)
    assert queued["status"] == "queued" and queued["hex_id"] == HEX and queued["revision"] == 1
    assert queued["world_id"] == WORLD and queued["model_id"] == WORLD_MODEL and queued["has_image"] is True
    row = sdb.world_vectors[namespace, world_hex_store.DOC_KIND, HEX, WORLD_MODEL]
    assert row.embedding is None and row.meta["tenant_id"] == TENANT and row.meta["revision"] == 1
    assert [j.status for j in sdb.jobs.values()] == ["pending"]

    # Decoys: another tenant's owned world, and a retired model space in our own namespace.
    act_as(monkeypatch, principal(tenant=OTHER_TENANT))
    foreign = await semantic_tools.index_world(world_id=FOREIGN_WORLD,
                                               payload=envelope(world=FOREIGN_WORLD, water="lake_lake_lake"))
    assert foreign["status"] == "queued"
    sdb.world_vectors[namespace, world_hex_store.DOC_KIND, "retired", "gemma-multimodal:retired:3"] = UnityWorldVector(
        world_id=namespace, doc_kind=world_hex_store.DOC_KIND, doc_id="retired", model="gemma-multimodal:retired:3",
        content_text="lake lake lake", content_hash="x", meta={"tenant_id": TENANT}, embedding=[0.0, 1.0, 0.0])
    act_as(monkeypatch, principal())

    assert await semantic_tools.search_world(world_id=WORLD, query="lake") == []  # pending ≠ indexed

    # Real worker dispatch → process_world_hex_jobs → revision-CAS completion → job done.
    await embedding_worker._drain_until_idle(10)
    assert {j.status for j in sdb.jobs.values()} == {"done"}
    assert row.embedding is not None and len(row.embedding) == 3
    joint = sdb.provider.calls[0][1][0]
    assert joint.media.mime_type == "image/png" and "serene_lake" in joint.text

    hits = await semantic_tools.search_world(world_id=WORLD, query="serene lake near willow trees", k=5)
    assert len(hits) == 1
    hit = hits[0]
    assert hit["hex_id"] == HEX and hit["center_pos"] == CENTER and hit["center_frame"] == "unity_xz_meters"
    assert "serene_lake" in hit["summary"] and WORLD in hit["summary"] and isinstance(hit["score"], float)

    response = await world_adapters.search_world_http(
        http(world_adapters.SEARCH_WORLD_PATH, {"query": "lake", "k": 3}, WORLD))
    assert response.status_code == 200
    data = body_of(response)
    assert data["status"] == "ok" and data["world_id"] == WORLD and data["results"] == [
        dict(hit, score=data["results"][0]["score"])]
    assert {model for _, model in sdb.search_calls} == {WORLD_MODEL}

    # The other tenant searching its own world sees only its own hex, never ours.
    act_as(monkeypatch, principal(tenant=OTHER_TENANT))
    theirs = await semantic_tools.search_world(world_id=FOREIGN_WORLD, query="lake")
    assert len(theirs) == 1 and "lake_lake_lake" in theirs[0]["summary"]
    act_as(monkeypatch, principal())

    # Identical envelope (same revision) is unchanged: no job, no provider call.
    calls, jobs = len(sdb.provider.calls), len(sdb.jobs)
    response = await world_adapters.index_world_http(http(world_adapters.INDEX_WORLD_PATH, envelope(), WORLD))
    assert response.status_code == 200 and body_of(response)["status"] == "unchanged"
    assert len(sdb.provider.calls) == calls and len(sdb.jobs) == jobs and row.embedding is not None


# ── 3. Registration, authentication, authorization, validation ───────────

def test_registration_scopes_and_no_legacy_route_collision():
    from core.context import http_route_registry
    from core.http_route_registry import AuthPolicy
    from core.proxy_tools.static_tool_loader import collect_from
    from plugins.world_semantic_plugin import routes

    routes.register_routes()
    decls = {d.path: d for d in http_route_registry._all_decls if d.owner == "world_semantic_plugin"}
    assert SEMANTIC_PATHS <= set(decls)
    for path in SEMANTIC_PATHS:
        assert decls[path].auth_policy is AuthPolicy.NONE and decls[path].methods == ("POST",)
    # Semantic aliases that collided with the legacy spatial namespace are gone.
    assert "/api/world/none/{world_id}/index" not in decls
    assert "/api/world/none/{world_id}/search" not in decls
    # Legacy spatial ingest / jobs contract is untouched.
    assert decls[routes.INDEX_PATH].endpoint is routes.world_index
    assert decls[routes.INDEX_PATH].auth_policy is AuthPolicy.PUBLIC
    assert decls[routes.JOBS_PATH].endpoint is routes.world_index_jobs_list

    tools = {d.operation_id: d for d in collect_from("world_semantic_plugin", ["plugins.world_semantic_plugin.MCPTools"])}
    for name, access in (("search_assets", "read"), ("search_world", "read"),
                         ("index_assets", "write"), ("index_world", "write")):
        assert tools[f"world_semantic_plugin__{name}"].access == access
    for legacy in ("query_context", "claim_hexes"):
        assert any(op.endswith(legacy) for op in tools)
    manifest = json.loads((Path(world_adapters.__file__).parent / "manifest.json").read_text())
    assert {"search_assets", "search_world", "index_assets", "index_world"} <= set(manifest["capabilities"])
    assert {READ, WRITE} <= {s["token"] for s in manifest["scopes"]}


OPERATIONS = (
    # (name, http handler, path template, http body, mcp call)
    ("search_assets", asset_adapters.search_assets_http, asset_adapters.SEARCH_PATH, {"query": "stone"},
     lambda w: semantic_tools.search_assets(world_id=w, query="stone")),
    ("index_assets", asset_adapters.index_assets_http, asset_adapters.INDEX_PATH, {"version": 1, "assets": []},
     lambda w: semantic_tools.index_assets(world_id=w, index={"version": 1, "assets": []})),
    ("search_world", world_adapters.search_world_http, world_adapters.SEARCH_WORLD_PATH, {"query": "lake"},
     lambda w: semantic_tools.search_world(world_id=w, query="lake")),
    ("index_world", world_adapters.index_world_http, world_adapters.INDEX_WORLD_PATH, envelope(),
     lambda w: semantic_tools.index_world(world_id=w, payload=envelope(world=w))),
)


def downstream_spies(monkeypatch):
    """Spy every post-authorization boundary: projection, services, store, enqueue, provider."""
    spies = {}
    targets = (
        (world_adapters, "projection_from_world_row"), (world_adapters, "search_world_service"),
        (world_adapters, "index_world_service"), (asset_adapters, "search_assets_service"),
        (asset_adapters, "index_assets_service"), (world_hex_store, "reserve_document"),
        (world_hex_store, "search_documents"), (asset_store, "stage_assets"),
    )
    for module, name in targets:
        original = getattr(module, name)
        spy = AsyncMock(side_effect=original) if name != "projection_from_world_row" else None
        if spy is None:
            calls = []

            def spy(*args, _original=original, _calls=calls, **kwargs):
                _calls.append(args)
                return _original(*args, **kwargs)
            spy.calls = calls
        monkeypatch.setattr(module, name, spy)
        spies[name] = spy
    return spies


def assert_untouched(spies, sdb, before, provider_calls, statements):
    for name, spy in spies.items():
        count = len(spy.calls) if name == "projection_from_world_row" else spy.await_count
        assert count == 0, f"{name} ran after denial"
    assert sdb.snapshot() == before and len(sdb.provider.calls) == provider_calls
    assert len(sdb.statements) == statements  # not a single SQL read or write after denial


@pytest.mark.asyncio
async def test_identity_and_scope_denials_for_all_four_operations(sdb, monkeypatch):
    spies = downstream_spies(monkeypatch)
    before, provider_calls, statements = sdb.snapshot(), len(sdb.provider.calls), len(sdb.statements)
    for name, handler, path, body, call in OPERATIONS:
        write = name.startswith("index")
        world = WORLD if name.endswith("world") else LIBRARY
        # Missing bearer / unresolvable bearer → 401; MCP without identity → PermissionError.
        act_as(monkeypatch, None)
        assert (await handler(http(path, body, world, bearer=False))).status_code == 401
        assert (await handler(http(path, body, world))).status_code == 401
        with pytest.raises(PermissionError, match="authentication"):
            await call(world)
        # Authenticated but tenantless principal → 403, never a default namespace.
        act_as(monkeypatch, principal(tenant=None))
        assert (await handler(http(path, body, world))).status_code == 403
        with pytest.raises(PermissionError, match="explicit tenant"):
            await call(world)
        # Wrong scope group: read token on write op, write token on read op.
        act_as(monkeypatch, principal(scopes=(READ,) if write else (WRITE,)))
        response = await handler(http(path, body, world))
        assert response.status_code == 403 and body_of(response)["status"] == "error"
        with pytest.raises(PermissionError, match="scope denied"):
            await call(world)
    assert sdb.lookups == []  # identity/scope denial happens before any world lookup
    assert_untouched(spies, sdb, before, provider_calls, statements)


@pytest.mark.asyncio
async def test_foreign_unowned_and_absent_worlds_are_indistinguishable_not_found(sdb, monkeypatch):
    act_as(monkeypatch, principal(scopes=("plugin:world_semantic_plugin",)))
    spies = downstream_spies(monkeypatch)
    before, provider_calls, statements = sdb.snapshot(), len(sdb.provider.calls), len(sdb.statements)
    world_ops = [op for op in OPERATIONS if op[0].endswith("world")]
    for name, handler, path, body, call in world_ops:
        responses, errors = [], []
        for world in (FOREIGN_WORLD, UNOWNED_WORLD, ABSENT_WORLD):
            response = await handler(http(path, dict(body, world_id=world) if "inspect" in body else body, world))
            responses.append((response.status_code, body_of(response)))
            with pytest.raises(LookupError) as raised:
                await call(world)
            errors.append((type(raised.value), str(raised.value)))
        assert responses == [(404, {"status": "error", "error": "world_not_found"})] * 3
        assert len(set(errors)) == 1 and errors[0] == (world_adapters.WorldNotFound, "world not found")
    # The authorization lookup itself ran with the principal's tenant, and nothing after it.
    assert set(sdb.lookups) == {(w, TENANT) for w in (FOREIGN_WORLD, UNOWNED_WORLD, ABSENT_WORLD)}
    assert_untouched(spies, sdb, before, provider_calls, statements)
    # The owner can still use its world: denial is per-tenant, not global.
    act_as(monkeypatch, principal(tenant=OTHER_TENANT))
    assert await semantic_tools.search_world(world_id=FOREIGN_WORLD, query="lake") == []


@pytest.mark.asyncio
async def test_request_cannot_choose_tenant_world_or_root(sdb, monkeypatch, tmp_path):
    monkeypatch.setitem(plugin_config.SETTINGS, "asset_ingestion_roots", {str(TENANT): {LIBRARY: str(tmp_path)}})
    act_as(monkeypatch, principal())
    spies = downstream_spies(monkeypatch)
    before, provider_calls, statements = sdb.snapshot(), len(sdb.provider.calls), len(sdb.statements)
    service_spies = {k: v for k, v in spies.items() if k != "projection_from_world_row"}
    cases = (
        (asset_adapters.search_assets_http, asset_adapters.SEARCH_PATH, {"query": "stone", "tenant_id": OTHER_TENANT}, LIBRARY),
        (asset_adapters.index_assets_http, asset_adapters.INDEX_PATH, {"version": 1, "assets": [], "tenant_id": TENANT}, LIBRARY),
        (asset_adapters.index_assets_http, asset_adapters.INDEX_PATH, {"version": 1, "assets": [], "trusted_root": "/etc"}, LIBRARY),
        (world_adapters.search_world_http, world_adapters.SEARCH_WORLD_PATH, {"query": "lake", "tenant_id": OTHER_TENANT}, WORLD),
        (world_adapters.index_world_http, world_adapters.INDEX_WORLD_PATH, envelope() | {"tenant_id": TENANT}, WORLD),
        (world_adapters.index_world_http, world_adapters.INDEX_WORLD_PATH, envelope() | {"trusted_root": "/etc"}, WORLD),
        (world_adapters.index_world_http, world_adapters.INDEX_WORLD_PATH, envelope(world=FOREIGN_WORLD), WORLD),
        (world_adapters.search_world_http, world_adapters.SEARCH_WORLD_PATH, b"{not json", WORLD),
    )
    for handler, path, body, world in cases:
        response = await handler(http(path, body, world))
        assert response.status_code == 400, (path, body if isinstance(body, bytes) else sorted(body))
    for bad in (envelope() | {"tenant_id": TENANT}, envelope() | {"trusted_root": "/etc"}, envelope(world=FOREIGN_WORLD)):
        with pytest.raises(ValueError):
            await semantic_tools.index_world(world_id=WORLD, payload=bad)
    # A projection patch in the envelope cannot replace the established one.
    response = await world_adapters.index_world_http(
        http(world_adapters.INDEX_WORLD_PATH, envelope() | {"projection": {"anchor_lat": 0.0}}, WORLD))
    assert response.status_code == 400
    # Asset references outside the server-configured root are rejected (no client roots).
    escape = {"version": 1, "assets": [asset_row("bridge", "prop", "bridge", ["stone"], "../outside.png")]}
    response = await asset_adapters.index_assets_http(http(asset_adapters.INDEX_PATH, escape, LIBRARY))
    assert response.status_code == 400
    # No caller-selectable tenant/root parameter exists on the exposed tool surface.
    import inspect
    for tool in (semantic_tools.search_assets, semantic_tools.index_assets,
                 semantic_tools.search_world, semantic_tools.index_world):
        assert not {"tenant_id", "trusted_root", "root", "projection"} & set(inspect.signature(tool).parameters)
    assert all(spy.await_count == 0 for name, spy in service_spies.items()
               if name not in ("index_world_service", "index_assets_service"))
    assert sdb.snapshot() == before and len(sdb.provider.calls) == provider_calls


@pytest.mark.asyncio
async def test_owned_world_store_lookup_and_migration_never_invent_ownership(monkeypatch):
    import importlib.util
    from plugins.world_semantic_plugin.stores import world_store

    executed = []

    class Session:
        async def execute(self, stmt, params=None):
            executed.append((str(stmt), params))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None), rowcount=0)

        async def commit(self):
            pass

    @asynccontextmanager
    async def session():
        yield Session()

    monkeypatch.setattr(world_store, "get_async_session", session)
    assert await world_store.get_world_for_tenant(WORLD, TENANT) is None
    sql, params = executed[-1]
    assert "world_id = :world_id AND tenant_id = :tenant_id" in sql and params == {"world_id": WORLD, "tenant_id": TENANT}
    for bad in (None, 0, -1, True, "7"):
        executed.clear()
        assert await world_store.get_world_for_tenant(WORLD, bad) is None and not executed
    assert await world_store.assign_world_tenant(WORLD, TENANT) is False
    assert "tenant_id IS NULL" in executed[-1][0]  # claims only unowned rows; never reassigns

    path = Path(world_store.__file__).parents[1] / "migrations/0010_world_tenant_owner.py"
    spec = importlib.util.spec_from_file_location("owner_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ddl = []
    module.upgrade(SimpleNamespace(execute=lambda s: ddl.append(str(s))))
    assert any("ADD COLUMN IF NOT EXISTS tenant_id BIGINT" in s for s in ddl)
    assert not any(word in s.upper() for s in ddl for word in ("UPDATE ", "DELETE", "NOT NULL", "DEFAULT"))
