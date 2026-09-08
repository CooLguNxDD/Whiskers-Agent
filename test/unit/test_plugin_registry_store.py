"""Unit tests for DBPluginRegistry content-hash / stale / delete helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db_layer.plugin_registry_store import DBPluginRegistry, PluginRecord, PluginMetaKey


def _record(
    plugin_id: str = "demo_plugin",
    *,
    meta: dict | None = None,
    is_active: bool = True,
    version: str = "1.0.0",
    content_hash: str | None = None,
) -> PluginRecord:
    return PluginRecord(
        id=plugin_id,
        display_name=plugin_id,
        version=version,
        meta=dict(meta or {}),
        is_active=is_active,
        registered_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        content_hash=content_hash,
    )


def _session_cm(execute_result=None):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result or MagicMock())
    session.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, session


@pytest.mark.asyncio
async def test_register_preserves_carried_meta_keys():
    store = DBPluginRegistry()
    existing = _record(
        content_hash="sha256:abc",
        meta={
            "skills": {"a.md": "skill"},
            PluginMetaKey.VERSION_HISTORY.value: [{"version": "1.0.0", "content_hash": "sha256:abc"}],
            PluginMetaKey.SCOPES.value: [{"token": "plugin:demo_plugin"}],
            PluginMetaKey.SCOPES_HASH.value: "fp1",
            PluginMetaKey.STALE.value: True,
            PluginMetaKey.STALE_SINCE.value: "2026-01-01T00:00:00+00:00",
        },
    )
    cm, session = _session_cm()

    with (
        patch.object(store, "get", new=AsyncMock(return_value=existing)),
        patch("db_layer.plugin_registry_store.get_async_session", return_value=cm),
    ):
        await store.register(
            {
                "name": "demo_plugin",
                "version": "2.0.0",
                "display_name": "Demo",
                "description": "new",
                "meta": {"description": "from_manifest"},
            }
        )

    params = session.execute.call_args[0][1]
    meta = json.loads(params["meta"])
    assert meta["skills"] == {"a.md": "skill"}
    # content_hash is a real column — not rewritten by register() and not in meta
    assert "content_hash" not in meta
    assert meta[PluginMetaKey.VERSION_HISTORY.value][0]["content_hash"] == "sha256:abc"
    assert meta[PluginMetaKey.SCOPES.value] == [{"token": "plugin:demo_plugin"}]
    assert meta[PluginMetaKey.SCOPES_HASH.value] == "fp1"
    assert meta[PluginMetaKey.STALE.value] is True
    assert meta[PluginMetaKey.STALE_SINCE.value] == "2026-01-01T00:00:00+00:00"
    assert meta["description"] == "new"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_content_hash_writes_column_and_history():
    store = DBPluginRegistry()
    rec = _record(
        content_hash="sha256:old",
        meta={
            PluginMetaKey.VERSION_HISTORY.value: [
                {
                    "version": "1.0.0",
                    "content_hash": "sha256:old",
                    "seen_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            PluginMetaKey.STALE.value: True,
            PluginMetaKey.STALE_SINCE.value: "2026-01-02T00:00:00+00:00",
            "content_hash": "sha256:legacy_meta",  # should be stripped
        },
    )
    cm, session = _session_cm()

    with (
        patch.object(store, "get", new=AsyncMock(return_value=rec)),
        patch("db_layer.plugin_registry_store.get_async_session", return_value=cm),
    ):
        await store.set_content_hash("demo_plugin", "sha256:new", "1.1.0")

    params = session.execute.call_args[0][1]
    assert params["content_hash"] == "sha256:new"
    meta = json.loads(params["meta"])
    assert "content_hash" not in meta  # column is SoT
    assert PluginMetaKey.STALE.value not in meta
    assert PluginMetaKey.STALE_SINCE.value not in meta
    assert len(meta[PluginMetaKey.VERSION_HISTORY.value]) == 2
    assert meta[PluginMetaKey.VERSION_HISTORY.value][-1]["version"] == "1.1.0"

    # Same hash again → history append still runs at store layer when called;
    # loader skips when unchanged. Store always rewrites column + re-runs append
    # which is a no-op when last entry matches.
    rec2 = _record(
        content_hash="sha256:new",
        meta={PluginMetaKey.VERSION_HISTORY.value: meta[PluginMetaKey.VERSION_HISTORY.value]},
    )
    cm2, session2 = _session_cm()
    with (
        patch.object(store, "get", new=AsyncMock(return_value=rec2)),
        patch("db_layer.plugin_registry_store.get_async_session", return_value=cm2),
    ):
        await store.set_content_hash("demo_plugin", "sha256:new", "1.1.1")

    meta2 = json.loads(session2.execute.call_args[0][1]["meta"])
    assert len(meta2[PluginMetaKey.VERSION_HISTORY.value]) == 2
    assert session2.execute.call_args[0][1]["content_hash"] == "sha256:new"


@pytest.mark.asyncio
async def test_mark_and_clear_stale_idempotent():
    store = DBPluginRegistry()
    # 1. Non-stale: should perform the update and transition to stale.
    res_success = MagicMock()
    res_success.rowcount = 1
    cm1, session1 = _session_cm(execute_result=res_success)

    with patch("db_layer.plugin_registry_store.get_async_session", return_value=cm1):
        assert await store.mark_stale("demo_plugin") is True

    call_args1 = session1.execute.call_args[0]
    query_str1 = str(call_args1[0])
    params1 = call_args1[1]
    assert "UPDATE plugins" in query_str1
    assert "jsonb_set" in query_str1
    assert params1["id"] == "demo_plugin"
    assert "ts" in params1

    # 2. Already stale (or not found): matches 0 rows (returns False)
    res_noop = MagicMock()
    res_noop.rowcount = 0
    cm2, session2 = _session_cm(execute_result=res_noop)

    with patch("db_layer.plugin_registry_store.get_async_session", return_value=cm2):
        assert await store.mark_stale("demo_plugin") is False

    # 3. Clear stale: should transition out of stale
    cm3, session3 = _session_cm(execute_result=res_success)
    with patch("db_layer.plugin_registry_store.get_async_session", return_value=cm3):
        assert await store.clear_stale("demo_plugin") is True

    call_args3 = session3.execute.call_args[0]
    query_str3 = str(call_args3[0])
    params3 = call_args3[1]
    assert "UPDATE plugins" in query_str3
    assert "COALESCE(meta" in query_str3
    assert params3["id"] == "demo_plugin"

    # 4. Clear stale when not stale: no write (returns False)
    cm4, session4 = _session_cm(execute_result=res_noop)
    with patch("db_layer.plugin_registry_store.get_async_session", return_value=cm4):
        assert await store.clear_stale("demo_plugin") is False


@pytest.mark.asyncio
async def test_mark_stale_executes_against_real_postgres():
    """Regression for the ``:ts::text`` bind-param bug (SQLAlchemy misparses the
    Postgres double-colon cast as a second bound parameter, which Postgres then
    rejects with 'syntax error at or near \":\"'). The mock-based test above
    never sends the query text to a real parser, so it can't catch this class of
    bug — this one runs ``mark_stale``/``clear_stale`` against the live test DB
    and asserts the ``meta.stale``/``stale_since`` flags actually flip.
    """
    from sqlalchemy import text as sql_text

    from db_layer.connection import get_async_session

    store = DBPluginRegistry()
    plugin_id = "test_mark_stale_regression_plugin"

    async with get_async_session() as db:
        await db.execute(
            sql_text(
                """
                INSERT INTO plugins (id, display_name, version, capabilities,
                                      required_credentials, external_oauth_providers,
                                      meta, is_active, registered_at, last_seen_at)
                VALUES (:id, :id, '0.0.0', '{}', '{}', '{}', '{}'::jsonb, true, now(), now())
                ON CONFLICT (id) DO UPDATE SET meta = '{}'::jsonb
                """
            ),
            {"id": plugin_id},
        )
        await db.commit()

    try:
        assert await store.mark_stale(plugin_id) is True

        record = await store.get(plugin_id)
        assert record is not None
        # jsonb_set('true', ...) stores a real JSON boolean, not the string "true".
        assert record.meta.get(PluginMetaKey.STALE.value) is True
        assert record.meta.get(PluginMetaKey.STALE_SINCE.value)  # ISO timestamp string, non-empty

        # Idempotent — already stale, no further write.
        assert await store.mark_stale(plugin_id) is False

        assert await store.clear_stale(plugin_id) is True
        cleared = await store.get(plugin_id)
        assert PluginMetaKey.STALE.value not in cleared.meta
        assert PluginMetaKey.STALE_SINCE.value not in cleared.meta
    finally:
        async with get_async_session() as db:
            await db.execute(sql_text("DELETE FROM plugins WHERE id = :id"), {"id": plugin_id})
            await db.commit()


@pytest.mark.asyncio
async def test_delete_plugin_cascade():
    store = DBPluginRegistry()
    rec = _record(meta={PluginMetaKey.STALE.value: True})
    cm, session = _session_cm()
    result = MagicMock()
    result.rowcount = 1
    session.execute = AsyncMock(return_value=result)
    unreg = MagicMock()

    with (
        patch.object(store, "get", new=AsyncMock(return_value=rec)),
        patch.object(store, "delete_plugin_route_embeddings", new=AsyncMock(return_value=2)) as emb,
        patch(
            "core.scope_management.registration.unregister_plugin_permissions",
            unreg,
        ),
        patch("db_layer.plugin_registry_store.get_async_session", return_value=cm),
    ):
        ok = await store.delete_plugin("demo_plugin")

    assert ok is True
    emb.assert_awaited_once_with("demo_plugin")
    unreg.assert_called_once_with("demo_plugin")
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_delete_plugin_missing_returns_false():
    store = DBPluginRegistry()
    with patch.object(store, "get", new=AsyncMock(return_value=None)):
        assert await store.delete_plugin("missing") is False
