"""Offline S03 contracts: actual Unity envelopes, queue plumbing and scoped search."""
from __future__ import annotations

import base64
import copy
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import h3
import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from plugins.world_semantic_plugin.hexmath import Projection, cell, cell_center_meters
from plugins.world_semantic_plugin.world_documents import AuthorizedWorld, normalize_document
from plugins.world_semantic_plugin import world_index
from plugins.world_semantic_plugin.stores import world_hex_store as store
from plugins.world_semantic_plugin.models import UnityWorldVector
from core_graph.worker import embedding_worker
from db_layer.embeddings import search_engine

SEL = {"provider": "gemma-multimodal", "model": "configured-test-alias", "dimensions": 3}
MODEL = "gemma-multimodal:configured-test-alias:3"
PROJ = Projection(anchor_lat=12, anchor_lng=25, base_res=10)
HEX = cell(450, 700, PROJ)
CENTER = list(cell_center_meters(HEX, PROJ))
CTX = AuthorizedWorld(tenant_id=7, world_id="demo", projection=PROJ)


def png(color="red"):
    out = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(out, format="PNG")
    return out.getvalue()


def payload(revision=1, image=True):
    raw = {
        "world_id": "demo", "hex_id": HEX, "revision": revision,
        "inspect": {"success": True, "message": "world_inspect", "data": {
            "ok": True, "center": CENTER, "radius": 1,
            "histogram": {"stone": 4, "grass": 2}, "sampledSolid": 6,
            "height": {"max": 5, "min": 1, "avg": 3},
            "slope": {"0-5": 3}, "waterPercent": 20, "waterLevel": 2,
            "propHistogram": {"oak_tree": 2, "crate": 1},
            "waterBodies": [{"id": "lake", "level": 2, "flow": [1, 0]}],
            "splines": [{"id": "road", "kind": "road", "points": [[1, 2], [3, 4]]}],
            "streaming": {"loaded": 99},
        }},
    }
    if image:
        raw["snapshot_center"] = CENTER
        raw["snapshot"] = {"success": True, "data": {
            "ok": True, "path": "C:/Unity/Temp/WorldSnapshots/a.png",
            "base64": base64.b64encode(png()).decode(), "width": 2, "height": 2,
            "sizeX": 2, "sizeZ": 2, "view": "top", "fallback": "cpu",
        }}
    return raw


def test_actual_envelopes_summary_and_legacy_no_invented_facts():
    doc = normalize_document(CTX, payload(), MODEL)
    assert doc.hex_id == HEX
    assert doc.center_pos == CENTER
    assert doc.media.data == png()
    for term in ("stone", "grass", "waterBodies", "lake", "flow", "splines", "road", "oak_tree", "demo", HEX):
        assert term in doc.summary
    assert "streaming" not in doc.summary
    old = payload(image=False)
    for name in ("waterBodies", "splines", "propHistogram"):
        del old["inspect"]["data"][name]
    legacy = normalize_document(CTX, old, MODEL)
    assert legacy.media is None
    assert "waterBodies" not in legacy.summary and "splines" not in legacy.summary
    assert "oak_tree" not in legacy.summary


def test_canonical_hash_ignores_unordered_metadata_but_not_points_or_image():
    a = payload()
    a["inspect"]["data"]["waterBodies"].append({"id": "river", "level": 3})
    b = copy.deepcopy(a)
    b["inspect"]["data"]["histogram"] = {"grass": 2, "stone": 4}
    b["inspect"]["data"]["waterBodies"].reverse()
    b["inspect"]["data"]["height"] = {"avg": 3, "min": 1, "max": 5}
    da, db = [normalize_document(CTX, p, MODEL) for p in (a, b)]
    assert da.summary == db.summary and da.content_hash == db.content_hash
    b["snapshot"]["data"]["base64"] = base64.b64encode(png("blue")).decode()
    assert normalize_document(CTX, b, MODEL).content_hash != da.content_hash
    b = copy.deepcopy(a)
    b["inspect"]["data"]["splines"][0]["points"].reverse()
    assert normalize_document(CTX, b, MODEL).content_hash != da.content_hash
    for name, value in (("histogram", {"sand": 10}), ("waterLevel", 9), ("propHistogram", {"fern": 3})):
        b = copy.deepcopy(a)
        b["inspect"]["data"][name] = value
        assert normalize_document(CTX, b, MODEL).content_hash != da.content_hash


def test_media_invalid_base64_mime_dimensions_and_trusted_root(tmp_path):
    for changes in ({"base64": "%%%"}, {"base64": base64.b64encode(b"fake PNG").decode()},
                    {"mime_type": "image/jpeg"}, {"width": 3}, {"mime_type": "audio/wav"}):
        p = payload()
        p["snapshot"]["data"].update(changes)
        with pytest.raises(ValueError):
            normalize_document(CTX, p, MODEL)
    p = payload()
    del p["snapshot"]["data"]["base64"]
    with pytest.raises(ValueError, match="root"):
        normalize_document(CTX, p, MODEL)
    (tmp_path / "tile.png").write_bytes(png())
    p["snapshot"]["data"]["path"] = "tile.png"
    assert normalize_document(CTX, p, MODEL, trusted_root=tmp_path).media.data == png()
    for path in ("../tile.png", "https://evil.test/tile.png", "/arbitrary.png"):
        p["snapshot"]["data"]["path"] = path
        with pytest.raises(ValueError):
            normalize_document(CTX, p, MODEL, trusted_root=tmp_path)
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(png())
    (tmp_path / "escape.png").symlink_to(outside)
    p["snapshot"]["data"]["path"] = "escape.png"
    with pytest.raises(ValueError):
        normalize_document(CTX, p, MODEL, trusted_root=tmp_path)


def test_selection_failure_and_malformed_inspect_fail_closed():
    for selection in ({"provider": "openai", "model": "embeddinggemma:300m", "dimensions": 3},
                      SEL | {"dimensions": None}, SEL | {"model": ""}):
        with pytest.raises(ValueError):
            store.validate_selection(selection)
    assert store.validate_selection(SEL | {"dimensions": "03"}) == (MODEL, 3)
    for changes in ({"histogram": {"stone": -1}}, {"height": {"min": 1, "max": 2, "avg": 3}},
                    {"waterPercent": 101}, {"waterDepth": -1}, {"columns": 1.5},
                    {"center": [float("nan"), 0]}, {"ok": "true"}):
        raw = payload()
        raw["inspect"]["data"].update(changes)
        with pytest.raises(ValueError):
            normalize_document(CTX, raw, MODEL)
    raw = payload()
    raw["inspect"]["success"] = False
    with pytest.raises(ValueError):
        normalize_document(CTX, raw, MODEL)
    # JPEG is real decoded media, not merely a MIME switch on PNG bytes.
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="JPEG")
    raw = payload()
    raw["snapshot"]["data"].update(mime_type="image/jpeg", base64=base64.b64encode(image.getvalue()).decode())
    assert normalize_document(CTX, raw, MODEL).media.data == image.getvalue()


def test_identity_projection_and_spatial_association_validation():
    for kwargs in ({"tenant_id": None}, {"tenant_id": True}, {"world_id": ""},
                   {"projection": Projection(meters_per_degree=0)},
                   {"projection": Projection(anchor_lat=90)},
                   {"projection": Projection(base_res=16)}):
        with pytest.raises(ValueError):
            AuthorizedWorld(**({"tenant_id": 7, "world_id": "demo", "projection": PROJ} | kwargs))
    for change in ({"world_id": "other"}, {"tenant_id": 8}, {"hex_id": "not-h3"},
                   {"hex_id": h3.cell_to_parent(HEX, 9)}, {"revision": 0},
                   {"projection": {"anchor_lat": 0}}):
        with pytest.raises(ValueError):
            normalize_document(CTX, payload() | change, MODEL)
    broad = payload()
    broad["inspect"]["data"]["radius"] = 10000
    with pytest.raises(ValueError, match="hex"):
        normalize_document(CTX, broad, MODEL)
    wrong = payload()
    wrong["snapshot_center"] = [CENTER[0] + 1000, CENTER[1]]
    with pytest.raises(ValueError):
        normalize_document(CTX, wrong, MODEL)
    derived = payload(image=False)
    del derived["hex_id"]
    assert normalize_document(CTX, derived, MODEL).hex_id == HEX
    assert CTX.namespace != AuthorizedWorld(8, "demo", PROJ).namespace
    assert CTX.namespace != AuthorizedWorld(7, "other", PROJ).namespace


class FakeSession:
    def __init__(self, row=None):
        self.row = row
        self.statements = []
        self.commit = AsyncMock()

    async def execute(self, stmt):
        self.statements.append(stmt)
        return SimpleNamespace(scalar_one=lambda: self.row, rowcount=1)


def session_factory(session):
    @asynccontextmanager
    async def factory():
        yield session
    return factory


@pytest.mark.asyncio
async def test_real_ingest_reservation_no_duplicate_changed_content_and_stale(monkeypatch):
    doc = normalize_document(CTX, payload(), MODEL)
    row = UnityWorldVector(world_id=CTX.namespace, doc_kind=store.DOC_KIND, doc_id=HEX,
                           model=MODEL, content_hash="", content_text="", meta={}, embedding=None)
    session = FakeSession(row)
    monkeypatch.setattr(store, "get_async_session", session_factory(session))
    monkeypatch.setattr(world_index, "resolve_unity_world_embedding", AsyncMock(return_value=SEL))
    result = await world_index.index_world(payload(), context=CTX)
    assert result["status"] == "queued"
    assert row.content_hash == doc.content_hash and row.meta["revision"] == 1
    inserts = [s for s in session.statements if getattr(s, "is_insert", False)]
    job_stmt = inserts[-1]
    params = job_stmt.compile(dialect=postgresql.dialect()).params
    job_payload = params["payload"]
    assert job_payload["model_id"] == MODEL and job_payload["namespace"] == CTX.namespace
    assert base64.b64decode(job_payload["image_base64"]) == png()
    embed = AsyncMock(return_value=[[1., 0., 0.]])
    monkeypatch.setattr(store, "embed_multimodal_with", embed)
    monkeypatch.setattr(store, "resolve_unity_world_embedding", AsyncMock(return_value=SEL))
    failed = AsyncMock()
    monkeypatch.setattr(embedding_worker, "_mark_failed", failed)
    await embedding_worker.process_batch([{
        "id": 22, "plugin_id": params["plugin_id"], "operation_id": params["operation_id"],
        "content_hash": params["content_hash"], "payload": job_payload,
    }])
    failed.assert_not_awaited()
    assert embed.call_args.args[1][0].media.data == png()
    completed = [s for s in session.statements if getattr(s, "is_update", False) and s.table.name == "unity_world_vectors"]
    assert completed[0].compile(dialect=postgresql.dialect()).params["embedding"] == [1., 0., 0.]
    row.embedding = [1., 0., 0.]  # fake DB applies the above real completion UPDATE
    session.statements.clear()
    assert (await world_index.index_world(payload(2), context=CTX))["status"] == "unchanged"
    assert not any(getattr(s, "table", None) is not None and s.table.name == "embedding_jobs" for s in session.statements)
    assert row.meta["revision"] == 2
    assert embed.await_count == 1  # identical ingest neither queued nor re-embedded
    assert (await world_index.index_world(payload(1), context=CTX))["status"] == "stale"
    changed = payload(3)
    changed["inspect"]["data"]["waterLevel"] = 8
    assert (await world_index.index_world(changed, context=CTX))["status"] == "queued"
    assert row.embedding is None and row.meta["revision"] == 3
    changed = payload(4)
    changed["snapshot"]["data"]["base64"] = base64.b64encode(png("blue")).decode()
    assert (await world_index.index_world(changed, context=CTX))["status"] == "queued"
    assert row.content_hash != doc.content_hash
    with pytest.raises(ValueError, match="revision"):
        await world_index.index_world(payload(4), context=CTX)


@pytest.mark.asyncio
async def test_registered_consumer_joint_bytes_retry_failure_and_stale_cas(monkeypatch):
    doc = normalize_document(CTX, payload(), MODEL)
    job = {"id": 10, "plugin_id": "world_semantic_plugin", "operation_id": store.OPERATION_ID,
           "content_hash": "queue-hash", "payload": doc.job_payload()}
    failed = AsyncMock()
    monkeypatch.setattr(store, "resolve_unity_world_embedding", AsyncMock(return_value=SEL))
    monkeypatch.setattr(embedding_worker, "_mark_failed", failed)
    embed = AsyncMock(side_effect=ValueError("offline failure"))
    monkeypatch.setattr(store, "embed_multimodal_with", embed)
    session = FakeSession()
    monkeypatch.setattr(store, "get_async_session", session_factory(session))
    await embedding_worker.process_batch([copy.deepcopy(job)])
    failed.assert_awaited_once()
    assert not session.statements and session.commit.await_count == 0
    embed.side_effect = None
    embed.return_value = [[1., 0., 0.]]
    failed.reset_mock()
    await embedding_worker.process_batch([copy.deepcopy(job)])
    passed_sel, inputs = embed.call_args.args
    assert passed_sel == SEL and inputs[0].text == doc.summary
    assert inputs[0].media.data == png() and inputs[0].media.mime_type == "image/png"
    failed.assert_not_awaited()
    assert session.commit.await_count == 1
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    params = session.statements[0].compile(dialect=postgresql.dialect()).params
    assert "UPDATE unity_world_vectors" in sql and "WHERE" in sql
    assert CTX.namespace in params.values() and MODEL in params.values()
    assert doc.content_hash in params.values() and 1 in params.values()  # revision CAS
    # Text-only is explicit; not a fake image, still same multimodal vector space.
    no_image = normalize_document(CTX, payload(2, image=False), MODEL)
    job["payload"] = no_image.job_payload()
    await embedding_worker.process_batch([copy.deepcopy(job)])
    assert embed.call_args.args[1][0].media is None
    # Width errors and model drift never commit or report indexed.
    before = session.commit.await_count
    embed.return_value = [[1., 2.]]
    await embedding_worker.process_batch([copy.deepcopy(job)])
    assert session.commit.await_count == before and failed.await_count == 1
    monkeypatch.setattr(store, "resolve_unity_world_embedding", AsyncMock(return_value=SEL | {"dimensions": 4}))
    await embedding_worker.process_batch([copy.deepcopy(job)])
    assert session.commit.await_count == before and failed.await_count == 2
    # The same operation name from a sibling plugin cannot invoke this consumer.
    alien = copy.deepcopy(job)
    alien["plugin_id"] = "other_plugin"
    calls = embed.await_count
    await embedding_worker.process_batch([alien])
    assert embed.await_count == calls and session.commit.await_count == before


@pytest.mark.asyncio
async def test_search_scoped_model_kind_topk_centers_order_and_empty(monkeypatch):
    neighbor = h3.grid_disk(HEX, 1)[1]
    captured = {}
    async def engine(spec, query, sel, top_k, *, extra_filters):
        captured.update(query=query, sel=sel, k=top_k, spec=spec)
        stmt = extra_filters(select(UnityWorldVector))
        params = stmt.compile(dialect=postgresql.dialect()).params
        assert CTX.namespace in params.values() and store.DOC_KIND in params.values()
        assert spec.vector_transform is None  # no padding/truncation to the global width
        return [{"doc_id": HEX, "content_text": "lake", "score": .9},
                {"doc_id": neighbor, "content_text": "road", "score": .6}]
    monkeypatch.setattr(store, "_engine_search", engine)
    monkeypatch.setattr(world_index, "resolve_unity_world_embedding", AsyncMock(return_value=SEL))
    result = await world_index.search_world("lake near village", 999, context=CTX)
    assert captured["k"] == 50 and captured["sel"] == SEL
    assert captured["query"] == "lake near village"
    assert [r["hex_id"] for r in result] == [HEX, neighbor]
    assert [r["summary"] for r in result] == ["lake", "road"]
    assert [r["score"] for r in result] == [.9, .6]
    assert result[0]["center_pos"] == CENTER
    assert result[0]["center_frame"] == "unity_xz_meters"
    monkeypatch.setattr(store, "_engine_search", AsyncMock(return_value=[]))
    assert await world_index.search_world("missing", context=CTX) == []
    for k in (0, -1, True, 1.5, "10"):
        with pytest.raises(ValueError):
            await world_index.search_world("lake", k, context=CTX)
    with pytest.raises(ValueError):
        await world_index.search_world(" ", context=CTX)
    with pytest.raises(ValueError):
        await world_index.search_world("lake", context=None)


@pytest.mark.asyncio
async def test_real_search_engine_query_embedding_and_sql_isolation(monkeypatch):
    # No mocked search engine: exercise S01 query embedding selection, SQL model
    # filters, namespace, width and ordering before LIMIT against a fake session.
    embed = AsyncMock(return_value=[1., 0., 0.])
    monkeypatch.setattr(search_engine, "embed_query_with", embed)
    monkeypatch.setattr(world_index, "resolve_unity_world_embedding", AsyncMock(return_value=SEL))
    statements = []
    row = SimpleNamespace(id=1, world_id=CTX.namespace, doc_kind=store.DOC_KIND,
                          doc_id=HEX, content_text="lake", meta={}, similarity=.95)
    class SearchSession:
        async def execute(self, stmt):
            statements.append(stmt)
            return SimpleNamespace(all=lambda: [row])
    monkeypatch.setattr(search_engine, "get_async_session", session_factory(SearchSession()))
    result = await world_index.search_world("a lake", 3, context=CTX)
    embed.assert_awaited_once_with(SEL, "a lake")
    assert result[0]["summary"] == "lake" and result[0]["score"] == .95
    compiled = statements[-1].compile(dialect=postgresql.dialect())
    assert MODEL in compiled.params.values() and CTX.namespace in compiled.params.values()
    assert store.DOC_KIND in compiled.params.values()
    sql = str(compiled)
    assert "ORDER BY" in sql and "LIMIT" in sql and "vector_dims" in sql
    assert "IS NOT NULL" in sql
    assert sql.index("WHERE") < sql.index("ORDER BY") < sql.index("LIMIT")
    for context in (AuthorizedWorld(8, "demo", PROJ), AuthorizedWorld(7, "other", PROJ)):
        await world_index.search_world("a lake", context=context)
        params = statements[-1].compile(dialect=postgresql.dialect()).params
        assert context.namespace in params.values() and CTX.namespace not in params.values()
    monkeypatch.setattr(world_index, "resolve_unity_world_embedding", AsyncMock(return_value=SEL | {"model": "second"}))
    await world_index.search_world("a lake", context=CTX)
    params = statements[-1].compile(dialect=postgresql.dialect()).params
    assert "gemma-multimodal:second:3" in params.values() and MODEL not in params.values()


@pytest.mark.asyncio
async def test_inflight_stale_revision_cannot_replace_newer_reserved_row(monkeypatch):
    old = normalize_document(CTX, payload(1), MODEL)
    job = {"id": 10, "plugin_id": "world_semantic_plugin", "operation_id": store.OPERATION_ID,
           "content_hash": "job-hash", "payload": old.job_payload()}
    row = UnityWorldVector(world_id=CTX.namespace, doc_kind=store.DOC_KIND, doc_id=HEX,
                           model=MODEL, content_hash=old.content_hash, content_text=old.summary,
                           meta={"revision": 1}, embedding=None)
    session = FakeSession(row)
    monkeypatch.setattr(store, "get_async_session", session_factory(session))
    monkeypatch.setattr(world_index, "resolve_unity_world_embedding", AsyncMock(return_value=SEL))
    monkeypatch.setattr(store, "resolve_unity_world_embedding", AsyncMock(return_value=SEL))
    original_execute = session.execute
    cas_outcomes = []
    async def execute(stmt):
        if getattr(stmt, "is_update", False) and stmt.table.name == "unity_world_vectors":
            compiled = stmt.compile(dialect=postgresql.dialect())
            # Interpret the real compiled revision/hash predicates as a mock DB.
            params = compiled.params
            permitted = (row.meta["revision"] == params["param_1"]
                         and row.content_hash == params["content_hash_1"])
            cas_outcomes.append(permitted)
            if permitted:
                row.embedding = params["embedding"]
            return SimpleNamespace(rowcount=int(permitted))
        return await original_execute(stmt)
    session.execute = execute
    async def embed(selection, inputs):
        newer = payload(2)
        newer["inspect"]["data"]["waterLevel"] = 12
        assert (await world_index.index_world(newer, context=CTX))["status"] == "queued"
        return [[1., 0., 0.]]
    monkeypatch.setattr(store, "embed_multimodal_with", embed)
    failed = AsyncMock()
    await store.process_world_hex_jobs([job], failed)
    failed.assert_not_awaited()
    assert cas_outcomes == [False]
    assert row.embedding is None and row.meta["revision"] == 2
    assert row.content_hash != old.content_hash and '12' in row.content_text
    # Replaying the identical failed/current revision requeues FAILED only, not
    # processing/done, so a retry cannot steal a live claim.
    newer = payload(2)
    newer["inspect"]["data"]["waterLevel"] = 12
    await world_index.index_world(newer, context=CTX)
    insert_stmt = [s for s in session.statements if getattr(s, "is_insert", False)][-1]
    compiled = insert_stmt.compile(dialect=postgresql.dialect())
    assert "WHERE embedding_jobs.status" in str(compiled) and "failed" in compiled.params.values()


def test_legacy_object_hex_layer_mapping_and_width_migration_preserves_rows():
    from plugins.world_semantic_plugin.stores.unity_world_vectors_store import _hex_id_from_hit
    assert _hex_id_from_hit("object", {}, {"hex_id": HEX}) == HEX
    assert _hex_id_from_hit("hex_layer", {"doc_id": HEX + "#L2"}, {}) == HEX
    assert _hex_id_from_hit("hex", {"doc_id": HEX}, {}) == HEX
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parents[1] / "migrations/0009_world_vector_spaces.py"
    spec = importlib.util.spec_from_file_location("width_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    module.upgrade(SimpleNamespace(execute=lambda s: statements.append(str(s))))
    assert any("TYPE vector" in s for s in statements)
    assert not any("DELETE" in s.upper() or "TRUNCATE" in s.upper() or "USING NULL" in s.upper() for s in statements)
