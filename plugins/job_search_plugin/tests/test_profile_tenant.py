"""Tenant isolation contracts for job applicant profiles and preference vectors."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql

from plugins.job_search_plugin import store


def test_create_profile_stamps_context_tenant_and_ignores_caller(monkeypatch):
    from core.context import current_tenant_id

    current_tenant_id.set(1)
    seen = {}

    class Session:
        def add(self, row):
            seen["row"] = row

        async def flush(self):
            pass

        async def commit(self):
            pass

    @asynccontextmanager
    async def session():
        yield Session()

    monkeypatch.setattr(store, "get_async_session", session)
    monkeypatch.setattr(store, "_profile_to_dict", lambda row: {"tenant_id": row.tenant_id})
    result = asyncio.run(store.create_profile(full_name="A", email="a", tenant_id=2))
    assert seen["row"].tenant_id == result["tenant_id"] == 1


@pytest.mark.parametrize("tenant", [None, 0, -1])
def test_profile_writes_fail_closed_without_positive_tenant(monkeypatch, tenant):
    from core.context import current_tenant_id

    current_tenant_id.set(tenant)
    async def no_write(*args, **kwargs):
        raise AssertionError("write should fail before opening a session")
    monkeypatch.setattr(store, "get_async_session", no_write)
    with pytest.raises(ValueError):
        asyncio.run(store.create_profile(full_name="A", email="a"))
    with pytest.raises(ValueError):
        asyncio.run(store.upsert_profile(5, full_name="A", email="a"))
    with pytest.raises(ValueError):
        asyncio.run(store.add_preference_embedding(1, "resume", "x", [0.1]))


def test_upsert_conflict_is_tenant_guarded(monkeypatch):
    from core.context import current_tenant_id
    current_tenant_id.set(7)
    captured = {}

    class Session:
        async def execute(self, stmt):
            captured["stmt"] = stmt
        async def commit(self):
            pass

    @asynccontextmanager
    async def session():
        yield Session()

    monkeypatch.setattr(store, "get_async_session", session)
    monkeypatch.setattr(store, "get_profile_scoped", lambda *args, **kwargs: asyncio.sleep(0, result={"id": args[0]}))
    asyncio.run(store.upsert_profile(3, full_name="A", email="a"))
    sql = str(captured["stmt"].compile(dialect=postgresql.dialect()))
    assert "tenant_id" in sql
    assert "WHERE" in sql.upper()
    assert "tenant_id =" in sql


def test_scoped_get_never_queries_null_tenant(monkeypatch):
    from core.context import current_tenant_id
    current_tenant_id.set(3)
    captured = {}

    class Result:
        def scalar_one_or_none(self):
            return None
    class Session:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return Result()
    @asynccontextmanager
    async def session():
        yield Session()
    monkeypatch.setattr(store, "get_async_session", session)
    assert asyncio.run(store.get_profile_scoped(1)) is None
    sql = str(captured["stmt"].compile(dialect=postgresql.dialect()))
    assert "tenant_id =" in sql and "IS NULL" not in sql.upper()


def test_migration_backfills_both_null_tenant_tables():
    import importlib.util
    from pathlib import Path

    path = Path("plugins/job_search_plugin/migrations/0006_profile_tenant_backfill.py")
    spec = importlib.util.spec_from_file_location("profile_tenant_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements = []

    class Connection:
        def execute(self, statement):
            statements.append(str(statement))

    migration.upgrade(Connection())
    joined = " ".join(statements)
    assert "UPDATE job_applicant_profiles SET tenant_id = 1 WHERE tenant_id IS NULL" in joined
    assert "UPDATE job_preference_embeddings SET tenant_id = 1 WHERE tenant_id IS NULL" in joined


def test_scoped_get_fails_closed_without_tenant(monkeypatch):
    from core.context import current_tenant_id
    current_tenant_id.set(None)
    async def no_query():
        raise AssertionError("must not query without tenant")
    monkeypatch.setattr(store, "get_async_session", no_query)
    assert asyncio.run(store.get_profile_scoped(1)) is None
