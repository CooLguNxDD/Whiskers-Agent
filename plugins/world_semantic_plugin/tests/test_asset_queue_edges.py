"""Additional offline asset queue/search/adapter integration and safety edges."""
import base64
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from plugins.world_semantic_plugin.tests.test_asset_index import (
    SEL, Result, asset, db, ingest, media,
)
from plugins.world_semantic_plugin import asset_index as api
from plugins.world_semantic_plugin import asset_adapters as adapters
from plugins.world_semantic_plugin.stores import asset_store as store
from core_graph.worker import embedding_worker as worker


@pytest.mark.asyncio
async def test_pending_no_duplicate_missing_file_arrival_and_model_width_change(tmp_path, db, monkeypatch):
    row = asset(reference={"image": "later.png"})
    await ingest(tmp_path, [row])
    pending = copy.deepcopy(db.jobs)
    assert (await ingest(tmp_path, [row]))["enqueued"] == 0
    assert len(db.jobs) == 1 and next(iter(db.jobs.values())).payload == next(iter(pending.values())).payload
    (tmp_path / "later.png").write_bytes(media())
    assert (await ingest(tmp_path, [row]))["enqueued"] == 1
    monkeypatch.setattr(api, "resolve_asset_embedding", AsyncMock(return_value=dict(SEL, dimensions=4)))
    assert (await ingest(tmp_path, [row]))["enqueued"] == 1
    assert len(db.jobs) == 3 and db.assets[7, "library", "bridge"].dimensions == 4


@pytest.mark.asyncio
async def test_unloaded_consumer_job_failure_can_be_reingested(tmp_path, db, monkeypatch):
    await ingest(tmp_path, [asset()])
    digest = next(iter(db.jobs))
    monkeypatch.setattr(worker, "get_async_session", db.session)
    store.unregister_consumer()
    await worker.process_batch([db.claimed(digest)])
    assert db.jobs[digest].status == "failed"
    store.register_consumer()
    assert (await ingest(tmp_path, [asset()]))["enqueued"] == 1
    assert len(db.jobs) == 1 and db.jobs[digest].status == "pending"


@pytest.mark.asyncio
async def test_interrupted_processing_claim_can_be_retried_without_duplicate_rows(tmp_path, db):
    from datetime import datetime, timedelta, timezone
    await ingest(tmp_path, [asset()])
    digest = next(iter(db.jobs))
    db.claimed(digest)
    db.jobs[digest].claimed_at = datetime.now(timezone.utc)
    assert (await ingest(tmp_path, [asset()]))["enqueued"] == 0
    generation = db.jobs[digest].payload["generation"]
    db.jobs[digest].claimed_at -= timedelta(hours=2)
    assert (await ingest(tmp_path, [asset()]))["enqueued"] == 1
    assert len(db.jobs) == 1 and len(db.assets) == 1
    assert db.jobs[digest].payload["generation"] != generation
    assert db.jobs[digest].status == "pending"


@pytest.mark.asyncio
async def test_consumer_pair_identity_never_steals_unity_or_other_plugin(tmp_path, db, monkeypatch):
    await ingest(tmp_path, [asset()])
    job = db.claimed(next(iter(db.jobs)))
    consume = AsyncMock()
    monkeypatch.setitem(worker._EMBEDDING_CONSUMERS, (store.PLUGIN_ID, store.OPERATION_ID), consume)
    mark_failed = AsyncMock()
    monkeypatch.setattr(worker, "_mark_failed", mark_failed)
    foreign = dict(job, plugin_id="other")
    await worker.process_batch([foreign])
    consume.assert_not_awaited()
    mark_failed.assert_awaited_once()
    # Existing Unity branch still resolves its own provider and uses its own statement builder.
    from plugins.world_semantic_plugin.stores import unity_world_vectors_store as unity
    monkeypatch.setattr(unity, "resolve_unity_world_embedding", AsyncMock(return_value=SEL))
    from db_layer.embeddings import embeddings_core
    embed = AsyncMock(return_value=[[1., 0., 0.]])
    monkeypatch.setattr(embeddings_core, "embed_documents_with", embed)
    built = []
    monkeypatch.setattr(worker, "_build_unity_world_vector_stmt", lambda job, vec, model: built.append((job, vec, model)))
    class Session:
        async def execute(self, stmt):
            return Result()
        async def commit(self):
            pass
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def session():
        yield Session()
    monkeypatch.setattr(worker, "get_async_session", session)
    unity_job = dict(job, operation_id="upsert_unity_world_vector", payload={"content_text": "Existing hex"})
    await worker.process_batch([unity_job])
    consume.assert_not_awaited()
    assert embed.call_args.args[1] == ["Existing hex"]
    assert built[0][0]["operation_id"] == "upsert_unity_world_vector"


@pytest.mark.asyncio
async def test_real_dense_sql_and_image_jpeg_boundary(monkeypatch):
    from db_layer.embeddings import search_engine
    from contextlib import asynccontextmanager
    executed = []
    class Session:
        async def execute(self, stmt):
            executed.append(stmt.compile(dialect=postgresql.dialect()))
            return Result([SimpleNamespace(asset_id="bridge", meta=asset(), similarity=.9)])
    @asynccontextmanager
    async def session():
        yield Session()
    monkeypatch.setattr(search_engine, "get_async_session", session)
    monkeypatch.setattr(api, "resolve_asset_embedding", AsyncMock(return_value=SEL))
    embed = AsyncMock(return_value=[[1., 0., 0.]])
    monkeypatch.setattr(api.embeddings_core, "embed_multimodal_with", embed)
    image = {"mime_type": "image/jpeg", "data": base64.b64encode(media("JPEG")).decode()}
    hits = await api.search_assets(image=image, tenant_id=7, world_id="library", kind="prop", tags=["river", "stone"], k=3)
    assert hits == [{"asset_id": "bridge", "metadata": asset(), "score": .9}]
    assert embed.call_args.args[1][0].media.data == media("JPEG")
    statement = executed[0]
    sql = str(statement)
    for clause in ("embedding IS NOT NULL", "model =", "tenant_id =", "world_id =", "status =", "kind =", "@>", "ORDER BY", "LIMIT"):
        assert clause in sql
    assert sql.index("tenant_id =") < sql.index("LIMIT")
    assert ["river", "stone"] in statement.params.values()
    assert "gemma-multimodal:synthetic:3" in statement.params.values()


@pytest.mark.asyncio
async def test_actual_config_rejects_text_provider_before_any_queue(tmp_path, monkeypatch):
    from core import llm_config_service
    monkeypatch.setattr(llm_config_service, "resolve_tool_embedding", AsyncMock(return_value={"provider": "openai", "model": "embeddinggemma:300m", "dimensions": 3}))
    stage = AsyncMock()
    monkeypatch.setattr(store, "stage_assets", stage)
    with pytest.raises(ValueError, match="multimodal"):
        await api.index_assets({"version": 1, "assets": [asset()]}, tenant_id=7, world_id="library", trusted_root=tmp_path)
    stage.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_selection_and_malformed_vectors_remain_retryable(tmp_path, db, monkeypatch):
    await ingest(tmp_path, [asset()])
    digest = next(iter(db.jobs))
    monkeypatch.setattr(api, "resolve_asset_embedding", AsyncMock(return_value=dict(SEL, model="changed")))
    embed = AsyncMock()
    monkeypatch.setattr(api.embeddings_core, "embed_multimodal_with", embed)
    await worker.process_batch([db.claimed(digest)])
    embed.assert_not_awaited()
    assert db.jobs[digest].status == "failed" and db.assets[7, "library", "bridge"].embedding is None
    monkeypatch.setattr(api, "resolve_asset_embedding", AsyncMock(return_value=SEL))
    for bad in ([], [[1., 0.]], [[True, 0., 0.]], [[0., 0., 0.]], [[1., 0., 0.], [1., 0., 0.]]):
        await ingest(tmp_path, [asset()])
        embed.return_value = bad
        await worker.process_batch([db.claimed(digest)])
        assert db.jobs[digest].status == "failed" and db.assets[7, "library", "bridge"].embedding is None
    with pytest.raises(ValueError, match="cancel"):
        api.aggregate_vectors([[1., 0., 0.], [-1., 0., 0.]], 3)


@pytest.mark.asyncio
async def test_http_auth_namespace_scope_and_size(monkeypatch):
    from starlette.requests import Request
    from core.interfaces.principal import Principal
    principal = Principal("synthetic", frozenset({"group:world_semantic_plugin:read"}), tenant_id=7)
    auth = SimpleNamespace(principal_from_bearer=AsyncMock(return_value=principal))
    monkeypatch.setattr(adapters, "get_auth_service", lambda: auth)
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(adapters, "search_assets_service", search)
    def request(body, authorization=b"Bearer synthetic"):
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        return Request({"type": "http", "method": "POST", "path": "/api/world/none/library/assets/search",
                        "headers": [(b"authorization", authorization)], "path_params": {"world_id": "library"}}, receive)
    assert (await adapters.search_assets_http(request(b'{"query":"river"}'))).status_code == 200
    assert search.call_args.kwargs["tenant_id"] == 7
    assert (await adapters.index_assets_http(request(b'{}'))).status_code == 403
    assert (await adapters.search_assets_http(request(b'{"query":"river","tenant_id":8}'))).status_code == 400
    auth.principal_from_bearer.return_value = Principal("synthetic", frozenset({"all"}))
    assert (await adapters.search_assets_http(request(b'{"query":"river"}'))).status_code == 403
    auth.principal_from_bearer.return_value = principal
    monkeypatch.setattr(adapters, "MAX_INDEX_BYTES", 1)
    monkeypatch.setattr(adapters, "MAX_MEDIA_BYTES", 1)
    assert (await adapters.search_assets_http(request(b'{"query":"river"}'))).status_code == 413
    assert search.await_count == 1


ASSET_GUARD_REVISION = "0009_require_asset_schema"
WORLD_VECTOR_REVISION = "0009_world_vector_spaces"


def _step_by_revision(steps, revision):
    """Select one migration step by full revision id, never by sort position."""
    matches = [s for s in steps if s.revision == revision]
    assert len(matches) == 1, f"expected exactly one {revision}, got {[s.revision for s in steps]}"
    return matches[0]


def _discover_plugin_steps():
    from pathlib import Path
    from db_layer.plugin_schema_migrator import PluginSchemaMigrator
    migrator = PluginSchemaMigrator()
    return migrator, migrator.discover_steps(Path("plugins/world_semantic_plugin/migrations"))


def test_asset_guard_selected_by_identity_when_world_vector_migration_coexists():
    # Both 0009 files ship together; the world-vector one sorts after the guard, so position is not identity.
    _, steps = _discover_plugin_steps()
    revisions = [s.revision for s in steps]
    assert ASSET_GUARD_REVISION in revisions and WORLD_VECTOR_REVISION in revisions
    assert len(set(revisions)) == len(revisions)
    assert revisions.index(ASSET_GUARD_REVISION) < revisions.index(WORLD_VECTOR_REVISION)
    guard = _step_by_revision(steps, ASSET_GUARD_REVISION)
    assert guard.path.name == f"{ASSET_GUARD_REVISION}.py"
    assert _step_by_revision(list(reversed(steps)), ASSET_GUARD_REVISION) is guard
    assert _step_by_revision(steps, WORLD_VECTOR_REVISION) is not guard


def test_lifecycle_guard_is_not_another_ddl_owner():
    migrator, steps = _discover_plugin_steps()
    step = _step_by_revision(steps, ASSET_GUARD_REVISION)
    class Connection:
        def __init__(self, exists):
            self.exists, self.sql = exists, []
        def execute(self, stmt):
            self.sql.append(str(stmt))
            return SimpleNamespace(scalar=lambda: self.exists)
    conn = Connection("world_asset_embeddings")
    migrator._apply_step(conn, store.PLUGIN_ID, step)
    assert conn.sql == ["SELECT to_regclass('world_asset_embeddings')"]
    with pytest.raises(RuntimeError, match="core_051"):
        migrator._apply_step(Connection(None), store.PLUGIN_ID, step)
