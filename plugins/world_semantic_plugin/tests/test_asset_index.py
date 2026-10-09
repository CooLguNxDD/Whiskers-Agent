"""Offline asset contracts: synthetic media, real queue SQL, mocked S01 transport."""
import base64
import copy
import io
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Insert, Update
from sqlalchemy.sql.selectable import Select

from plugins.world_semantic_plugin import asset_index as api
from plugins.world_semantic_plugin.stores import asset_store as store
from core_graph.worker import embedding_worker as worker

SEL = {"provider": "gemma-multimodal", "model": "synthetic", "dimensions": 3}


def media(fmt="PNG", color="red"):
    buf = io.BytesIO()
    if fmt == "WAV":
        with wave.open(buf, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(8000)
            out.writeframes(b"\x00\x00" * 8)
    else:
        Image.new("RGB", (2, 2), color).save(buf, format=fmt)
    return buf.getvalue()


def asset(**changes):
    row = {"id": "bridge", "kind": "prop", "description": "Stone bridge over a river",
           "tags": ["stone", "river"], "craftRoles": ["crossing"], "bounds": [4, 2, 3],
           "variants": [{"name": "hunyuan2", "path": "Assets/bridge.glb", "sha256": "a" * 64}],
           "defaultVariant": "hunyuan2", "reference": {"image": None, "audio": None}}
    row.update(changes)
    return row


class Result:
    def __init__(self, rows=(), count=0):
        self.rows, self.rowcount = list(rows), count
    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None
    def all(self):
        return self.rows


class Database:
    """Small transactional SQL double, not an alternate production store."""
    def __init__(self):
        self.assets, self.jobs, self.statements = {}, {}, []
        self.commits = 0
        self.fail_commit = False

    @asynccontextmanager
    async def session(self):
        before = copy.deepcopy((self.assets, self.jobs))
        try:
            yield self
        except BaseException:
            self.assets, self.jobs = before
            raise

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("synthetic commit failure")
        self.commits += 1

    async def execute(self, stmt, parameters=None):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql, p = str(compiled), compiled.params
        self.statements.append((sql, p))
        if stmt is worker._CLAIM_SQL:
            from datetime import datetime, timezone
            claimed = []
            for row in self.jobs.values():
                if row.status == "pending" and len(claimed) < parameters["batch_size"]:
                    row.status = "processing"
                    row.claimed_at = datetime.now(timezone.utc)
                    row.attempts += 1
                    claimed.append(copy.deepcopy(row))
            return Result(claimed)
        if not isinstance(stmt, (Insert, Update, Select)):
            return Result()
        name = stmt.table.name if isinstance(stmt, (Insert, Update)) else ""
        if isinstance(stmt, Insert) and name == "world_asset_embeddings":
            key = (p["tenant_id"], p["world_id"], p["asset_id"])
            self.assets[key] = SimpleNamespace(**p)
            return Result()
        if isinstance(stmt, Insert) and name == "embedding_jobs":
            key = p["content_hash"]
            row = self.jobs.get(key)
            if row is None:
                row = SimpleNamespace(id=len(self.jobs) + 1, attempts=0)
            for field in ("plugin_id", "operation_id", "content_hash", "payload"):
                setattr(row, field, p[field])
            row.status = "pending"
            self.jobs[key] = row
            return Result()
        if isinstance(stmt, Select):
            if "FROM world_asset_embeddings" in sql:
                key = (p["tenant_id_1"], p["world_id_1"], p["asset_id_1"])
                return Result([self.assets[key]] if key in self.assets else [])
            if "FROM embedding_jobs" in sql:
                if "content_hash_1" in p:
                    row = self.jobs.get(p["content_hash_1"])
                    if row and (row.plugin_id != p["plugin_id_1"] or row.operation_id != p["operation_id_1"]):
                        row = None
                else:
                    row = next((j for j in self.jobs.values() if j.id == p["id_1"]), None)
                return Result([row] if row else [])
        if isinstance(stmt, Update) and name == "world_asset_embeddings":
            key = (p["tenant_id_1"], p["world_id_1"], p["asset_id_1"])
            row = self.assets.get(key)
            if row and ("generation_1" not in p or row.generation == p["generation_1"]):
                for field in ("content_hash", "model", "generation", "meta", "content_text", "embedding", "status"):
                    if field in p:
                        setattr(row, field, p[field])
                return Result(count=1)
            return Result()
        if isinstance(stmt, Update) and name == "embedding_jobs":
            for row in self.jobs.values():
                ids = p.get("id_1", [])
                if row.id == ids or (isinstance(ids, list) and row.id in ids):
                    for field in ("status", "last_error"):
                        if field in p:
                            setattr(row, field, p[field])
            return Result(count=1)
        raise AssertionError(f"Unhandled SQL: {sql}")

    def claimed(self, digest):
        j = self.jobs[digest]
        j.status = "processing"
        return copy.deepcopy(vars(j))


@pytest.fixture
def db(monkeypatch):
    database = Database()
    monkeypatch.setattr(store, "get_async_session", database.session)
    monkeypatch.setattr(api, "resolve_asset_embedding", AsyncMock(return_value=SEL))
    store.register_consumer()
    yield database
    store.unregister_consumer()


async def ingest(root, rows, tenant=7, world="library"):
    return await api.index_assets({"version": 1, "assets": rows}, tenant_id=tenant,
                                  world_id=world, trusted_root=root)


@pytest.mark.asyncio
async def test_schema_metadata_additive_fields_and_isolated_persistence(tmp_path, db):
    row = asset(future={"arbitrary": True})
    result = await ingest(tmp_path, [row])
    assert result["status"] == "enqueued" and result["enqueued"] == 1
    stored = db.assets[7, "library", "bridge"]
    assert stored.meta["variants"] == row["variants"] and stored.meta["bounds"] == [4, 2, 3]
    assert "river" in stored.content_text and "crossing" in stored.content_text
    assert stored.embedding is None and stored.status == "enqueued"
    assert "future" not in stored.meta
    await ingest(tmp_path, [row], tenant=8)
    await ingest(tmp_path, [row], world="other")
    assert len(db.assets) == len(db.jobs) == 3
    assert all(j.operation_id == store.OPERATION_ID for j in db.jobs.values())


@pytest.mark.asyncio
async def test_invalid_records_and_collection_are_not_success(tmp_path, db):
    for body in ({}, {"version": True, "assets": []}, {"version": 1, "assets": {}},
                 {"version": 1, "assets": [asset(), asset()]},
                 {"version": 1, "assets": [asset(kind="mesh")]},
                 {"version": 1, "assets": [asset(id="../bad")]},
                 {"version": 1, "assets": [asset(tags="river")]},
                 {"version": 1, "assets": [asset(defaultVariant="missing")]},
                 {"version": 1, "assets": [asset(bounds=[1, float("nan"), 3])]}):
        with pytest.raises(ValueError):
            await api.index_assets(body, tenant_id=7, world_id="library", trusted_root=tmp_path)
    assert not db.jobs and not db.assets


@pytest.mark.asyncio
async def test_media_bytes_png_jpeg_wav_and_both_aggregation(tmp_path, db, monkeypatch):
    for name, fmt in (("a.png", "PNG"), ("b.jpg", "JPEG"), ("c.wav", "WAV")):
        (tmp_path / name).write_bytes(media(fmt))
    rows = [asset(id="png", reference={"image": "a.png"}),
            asset(id="jpeg", reference={"image": "b.jpg"}),
            asset(id="wav", reference={"audio": "c.wav"}),
            asset(id="both", reference={"image": "a.png", "audio": "c.wav"})]
    embed = AsyncMock(side_effect=lambda sel, inputs: [[3., 0., 0.] if i.media.mime_type.startswith("image") else [0., 4., 0.] for i in inputs])
    monkeypatch.setattr(api.embeddings_core, "embed_multimodal_with", embed)
    await ingest(tmp_path, rows)
    for digest in list(db.jobs):
        await worker.process_batch([db.claimed(digest)])
    calls = [i for call in embed.call_args_list for i in call.args[1]]
    assert len(calls) == 5
    # Producer sorts asset identities for lock ordering; per-document media order stays image/audio.
    assert [i.media.mime_type for i in calls] == ["image/png", "audio/wav", "image/jpeg", "image/png", "audio/wav"]
    assert calls[0].media.data == media("PNG") and calls[1].media.data == media("WAV")
    assert calls[2].media.data == media("JPEG")
    assert all(i.text and "crossing" in i.text for i in calls)
    assert db.assets[7, "library", "both"].embedding == pytest.approx([2**-0.5, 2**-0.5, 0])
    assert all(j.status == "done" for j in db.jobs.values())
    assert all(a.status == "indexed" for a in db.assets.values())


@pytest.mark.asyncio
async def test_idempotence_metadata_and_same_path_media_change(tmp_path, db, monkeypatch):
    (tmp_path / "a.png").write_bytes(media())
    row = asset(reference={"image": "a.png"})
    await ingest(tmp_path, [row])
    first_hash = next(iter(db.jobs))
    monkeypatch.setattr(api.embeddings_core, "embed_multimodal_with", AsyncMock(return_value=[[1., 0., 0.]]))
    await worker.process_batch([db.claimed(first_hash)])
    result = await ingest(tmp_path, [dict(row, tags=list(reversed(row["tags"])) )])
    assert result["enqueued"] == 0 and result["indexed"] == 1
    assert len(db.jobs) == 1 and db.assets[7, "library", "bridge"].embedding == [1., 0., 0.]
    row["description"] = "A wooden river crossing"
    assert (await ingest(tmp_path, [row]))["enqueued"] == 1
    assert db.assets[7, "library", "bridge"].embedding is None
    (tmp_path / "a.png").write_bytes(media(color="blue"))
    assert (await ingest(tmp_path, [row]))["enqueued"] == 1
    assert len(db.assets) == 1 and len(db.jobs) == 3


@pytest.mark.asyncio
async def test_failure_retry_and_stale_jobs_including_reversion(tmp_path, db, monkeypatch):
    await ingest(tmp_path, [asset()])
    a = next(iter(db.jobs))
    old = db.claimed(a)
    embed = AsyncMock(side_effect=ValueError("synthetic provider failure"))
    monkeypatch.setattr(api.embeddings_core, "embed_multimodal_with", embed)
    await worker.process_batch([old])
    assert db.jobs[a].status == "failed" and db.assets[7, "library", "bridge"].status == "failed"
    assert db.assets[7, "library", "bridge"].embedding is None
    assert (await ingest(tmp_path, [asset()]))["enqueued"] == 1
    assert len(db.jobs) == 1
    pending_a = db.claimed(a)
    await ingest(tmp_path, [asset(description="newer")])
    b = db.assets[7, "library", "bridge"].content_hash
    embed.side_effect = None
    embed.return_value = [[1., 0., 0.]]
    await worker.process_batch([pending_a])
    assert db.assets[7, "library", "bridge"].content_hash == b
    assert db.assets[7, "library", "bridge"].embedding is None
    await ingest(tmp_path, [asset()])
    new_generation = db.jobs[a].payload["generation"]
    await worker.process_batch([old])
    assert db.jobs[a].status == "pending" and db.jobs[a].payload["generation"] == new_generation
    await worker.process_batch([db.claimed(a)])
    assert db.assets[7, "library", "bridge"].status == "indexed"


@pytest.mark.asyncio
async def test_atomic_enqueue_and_completion_failure(tmp_path, db, monkeypatch):
    db.fail_commit = True
    with pytest.raises(RuntimeError):
        await ingest(tmp_path, [asset()])
    assert not db.assets and not db.jobs
    db.fail_commit = False
    await ingest(tmp_path, [asset()])
    digest = next(iter(db.jobs))
    monkeypatch.setattr(api.embeddings_core, "embed_multimodal_with", AsyncMock(return_value=[[1., 0., 0.]]))
    original_commit = db.commit
    count = 0
    async def fail_once():
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError("synthetic write failure")
        await original_commit()
    monkeypatch.setattr(db, "commit", fail_once)
    await worker.process_batch([db.claimed(digest)])
    assert db.jobs[digest].status == "failed"
    assert db.assets[7, "library", "bridge"].embedding is None


@pytest.mark.asyncio
async def test_text_image_search_filters_before_limit_and_model_space(tmp_path, db, monkeypatch):
    captured = []
    async def dense(spec, vector, k, model, filters, row_filter):
        from sqlalchemy import select
        stmt = filters(select(*spec.select_cols).where(spec.model_col == model).limit(k))
        captured.append(stmt.compile(dialect=postgresql.dialect()))
        return [{"asset_id": "bridge", "metadata": asset(), "score": .75}]
    monkeypatch.setattr(api, "search_by_vector", dense)
    embed = AsyncMock(return_value=[[1., 0., 0.]])
    monkeypatch.setattr(api.embeddings_core, "embed_multimodal_with", embed)
    result = await api.search_assets("river bridge", tenant_id=7, world_id="library", kind="prop", tags=["river"], k=2)
    assert result[0]["asset_id"] == "bridge" and result[0]["score"] == .75
    assert embed.call_args.args[1][0].text == "river bridge"
    for expected in (7, "library", "prop", ["river"], 2, "gemma-multimodal:synthetic:3", "indexed"):
        assert expected in captured[0].params.values()
    image = {"mime_type": "image/png", "data": base64.b64encode(media()).decode()}
    await api.search_assets(image=image, tenant_id=8, world_id="library", k=1)
    assert embed.call_args.args[1][0].media.data == media()
    assert 8 in captured[1].params.values()
    monkeypatch.setattr(api, "resolve_asset_embedding", AsyncMock(return_value=dict(SEL, model="other")))
    monkeypatch.setattr(api, "search_by_vector", AsyncMock(return_value=[]))
    assert await api.search_assets("none", tenant_id=7, world_id="library") == []
    assert api.search_by_vector.call_args.args[3] == "gemma-multimodal:other:3"


@pytest.mark.asyncio
async def test_namespace_paths_invalid_media_and_query_shape(tmp_path, db):
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(media())
    (tmp_path / "escape.png").symlink_to(outside)
    for path in ("../outside.png", str(outside), "https://invalid.test/a.png", "escape.png", "a.gif"):
        with pytest.raises(ValueError):
            await ingest(tmp_path, [asset(reference={"image": path})])
    (tmp_path / "bad.png").write_bytes(b"not PNG")
    with pytest.raises(ValueError):
        await ingest(tmp_path, [asset(reference={"image": "bad.png"})])
    for tenant in (None, 0, True, "7"):
        with pytest.raises(ValueError):
            await ingest(tmp_path, [asset()], tenant=tenant)
    for kwargs in ({}, {"query": ""}, {"query": "x", "image": {}}, {"image": {"mime_type": "image/png", "data": "!"}},
                   {"query": "x", "k": 101}, {"query": "x", "kind": "mesh"}, {"query": "x", "tags": "river"}):
        with pytest.raises(ValueError):
            await api.search_assets(tenant_id=7, world_id="library", **kwargs)
    assert not db.jobs


@pytest.mark.asyncio
async def test_missing_optional_media_documented_and_bad_vectors_retryable(tmp_path, db, monkeypatch):
    result = await ingest(tmp_path, [asset(reference={"image": "missing.png"})])
    assert result["warnings"] == [{"asset_id": "bridge", "missing_media": ["image"]}]
    embed = AsyncMock(return_value=[[float("nan"), 0., 0.]])
    monkeypatch.setattr(api.embeddings_core, "embed_multimodal_with", embed)
    digest = next(iter(db.jobs))
    await worker.process_batch([db.claimed(digest)])
    assert embed.call_args.args[1][0].media is None
    assert db.jobs[digest].status == "failed" and db.assets[7, "library", "bridge"].embedding is None


@pytest.mark.asyncio
async def test_claim_to_s01_transport_to_vector_publication_uses_immutable_media(tmp_path, db, monkeypatch):
    """Exercise the existing claim API and actual S01 serialization, mocking transport only."""
    from contextlib import asynccontextmanager
    from core.proxy import ssrf_safety
    from db_layer.embeddings.multimodal import GemmaMultimodalEmbeddings

    original_image, original_audio = media(), media("WAV")
    (tmp_path / "ref.png").write_bytes(original_image)
    (tmp_path / "ref.wav").write_bytes(original_audio)
    await ingest(tmp_path, [asset(reference={"image": "ref.png", "audio": "ref.wav"})])
    # A job is a validated snapshot, not a deferred file read.
    (tmp_path / "ref.png").write_bytes(media(color="blue"))
    (tmp_path / "ref.wav").unlink()
    monkeypatch.setattr(worker, "get_async_session", db.session)
    jobs = await worker._claim_batch(1)
    assert len(jobs) == 1 and jobs[0]["operation_id"] == store.OPERATION_ID
    assert next(iter(db.jobs.values())).status == "processing"
    assert next(iter(db.jobs.values())).attempts == 1
    assert await worker._claim_batch(1) == []

    sent = []
    class Transport:
        async def post(self, endpoint, *, json, headers):
            sent.append((endpoint, json))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
                "data": [{"index": 1, "embedding": [0., 4., 0.]},
                         {"index": 0, "embedding": [3., 0., 0.]}]})
    @asynccontextmanager
    async def client(**kwargs):
        yield Transport()
    monkeypatch.setattr(ssrf_safety, "_safe_async_client", client)
    monkeypatch.setattr(ssrf_safety, "_is_safe_url", AsyncMock(return_value=True))
    real_client = GemmaMultimodalEmbeddings("synthetic", 3, None, "https://synthetic.invalid/v1")
    monkeypatch.setattr(api.embeddings_core, "_get_client_for", AsyncMock(return_value=real_client))
    await worker.process_batch(jobs)
    assert len(sent) == 1
    endpoint, wire = sent[0]
    assert endpoint == "https://synthetic.invalid/v1/embeddings"
    assert wire["model"] == "synthetic" and wire["dimensions"] == 3
    image_parts, audio_parts = [item["content"] for item in wire["input"]]
    assert image_parts[0] == audio_parts[0] == {"type": "text", "text": jobs[0]["payload"]["content_text"]}
    assert base64.b64decode(image_parts[1]["image_url"]["url"].split(",", 1)[1]) == original_image
    assert base64.b64decode(audio_parts[1]["input_audio"]["data"]) == original_audio
    assert next(iter(db.jobs.values())).status == "done"
    assert db.assets[7, "library", "bridge"].status == "indexed"
    assert db.assets[7, "library", "bridge"].embedding == pytest.approx([2**-0.5, 2**-0.5, 0])
    assert await worker._claim_batch(1) == []


def test_alembic_chain_ddl_and_no_duplicate_plugin_ownership():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    revision = script.get_revision("core_051")
    # The existing core directory has a separate legacy head; preserve it, do not rewrite history.
    assert revision.down_revision == "core_050"
    assert set(script.get_heads()) == {"568ee6fe3f3f", "core_051"}
    core_cfg = Config("alembic.ini")
    core_cfg.set_main_option("version_locations", "migrations/versions/core")
    assert set(ScriptDirectory.from_config(core_cfg).get_heads()) == {"568ee6fe3f3f", "core_051"}
    chain = list(script.iterate_revisions("core_051", "core_049"))
    assert [r.revision for r in chain] == ["core_051", "core_050"]
    stream = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": stream})
    with Operations.context(context):
        revision.module.upgrade()
    sql = stream.getvalue()
    for expected in ("CREATE TABLE world_asset_embeddings", "tenant_id BIGINT NOT NULL", "world_id", "asset_id", "generation", "embedding VECTOR", "world_asset_identity", "vector_dims(embedding)", "dimensions", "CHECK"):
        assert expected in sql
    local = Path("plugins/world_semantic_plugin/migrations")
    assert not any("CREATE TABLE world_asset_embeddings" in p.read_text() for p in local.glob("*.py"))


@pytest.mark.asyncio
async def test_thin_adapters_fail_closed_and_do_not_accept_tenant(monkeypatch):
    from plugins.world_semantic_plugin import asset_adapters as adapters
    from core.interfaces.principal import Principal
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(adapters, "search_assets_service", search)
    monkeypatch.setattr(adapters, "principal_for_tool", AsyncMock(return_value=None))
    with pytest.raises(PermissionError):
        await adapters.search_assets("library", query="river")
    monkeypatch.setattr(adapters, "principal_for_tool", AsyncMock(return_value=Principal("user", frozenset({"group:world_semantic_plugin:read"}), tenant_id=7)))
    assert await adapters.search_assets("library", query="river") == []
    assert search.call_args.kwargs["tenant_id"] == 7
    monkeypatch.setattr(adapters, "principal_for_tool", AsyncMock(return_value=Principal("user", frozenset(), tenant_id=7)))
    with pytest.raises(PermissionError):
        await adapters.search_assets("library", query="river")
    assert "tenant_id" not in __import__("inspect").signature(adapters.search_assets).parameters
    request = SimpleNamespace(headers={}, path_params={"world_id": "library"})
    response = await adapters.search_assets_http(request)
    assert response.status_code == 401
