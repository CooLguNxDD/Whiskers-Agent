"""Unit tests for extracted db_layer analytics/route/config stores."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _session_cm(execute_result=None, side_effect=None):
    """Build an async context manager mock for get_async_session."""
    session = AsyncMock()
    if side_effect is not None:
        session.execute = AsyncMock(side_effect=side_effect)
    else:
        session.execute = AsyncMock(return_value=execute_result)
    session.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, session


@pytest.mark.asyncio
async def test_get_kpi_metrics_returns_mapping():
    from db_layer import analytics_store as store

    row = {"total_calls": 10, "success_calls": 9, "p50": 12, "p99": 40}
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    cm, _ = _session_cm(execute_result=result)

    with patch.object(store, "get_async_session", return_value=cm):
        out = await store.get_kpi_metrics(datetime.now(timezone.utc), tenant_id=1)

    assert out == row
    assert out["total_calls"] == 10


@pytest.mark.asyncio
async def test_get_top_tools_passes_limit():
    from db_layer import analytics_store as store

    result = MagicMock()
    result.mappings.return_value.all.return_value = [
        {"tool_name": "run_graph", "calls": 5, "p99": 100},
    ]
    cm, session = _session_cm(execute_result=result)

    with patch.object(store, "get_async_session", return_value=cm):
        out = await store.get_top_tools(datetime.now(timezone.utc), tenant_id=1, limit=3)

    assert len(out) == 1
    assert out[0]["tool_name"] == "run_graph"
    # second arg to execute is the bind params dict
    args, kwargs = session.execute.call_args
    stmt = args[0]
    if hasattr(stmt, "_limit_clause") and stmt._limit_clause is not None:
        limit_val = stmt._limit_clause.value
    else:
        params = args[1] if len(args) > 1 else kwargs.get("parameters") or kwargs
        if not isinstance(params, dict):
            params = session.execute.call_args[0][1]
        limit_val = params.get("limit")
    assert limit_val == 3


@pytest.mark.asyncio
async def test_set_plugin_routes_enabled_returns_rowcount():
    from db_layer import route_store as store

    result = MagicMock()
    result.rowcount = 4
    cm, session = _session_cm(execute_result=result)

    with patch.object(store, "get_async_session", return_value=cm):
        n = await store.set_plugin_routes_enabled("fake_plugin", True)

    assert n == 4
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_route_enabled_returns_rowcount():
    from db_layer import route_store as store

    result = MagicMock()
    result.rowcount = 1
    cm, _ = _session_cm(execute_result=result)

    with patch.object(store, "get_async_session", return_value=cm):
        n = await store.set_route_enabled("fake_plugin", 42, False)

    assert n == 1


@pytest.mark.asyncio
async def test_delete_plugin_route_embeddings_deletes_both_tables():
    from db_layer import route_store as store

    emb_result = MagicMock()
    emb_result.rowcount = 2
    jobs_result = MagicMock()
    jobs_result.rowcount = 1
    cm, session = _session_cm(side_effect=[emb_result, jobs_result])

    with patch.object(store, "get_async_session", return_value=cm):
        n = await store.delete_plugin_route_embeddings("proxy_foo")

    assert n == 2
    assert session.execute.await_count == 2
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_llm_settings_filters_allowed_keys():
    from db_layer import config_store as store

    result = MagicMock()
    result.fetchone.return_value = (
        {"llm_provider": "openai", "secret": "nope", "llm_model": "gpt-4"},
    )
    cm, _ = _session_cm(execute_result=result)

    with patch.object(store, "get_async_session", return_value=cm):
        out = await store.get_llm_settings({"llm_provider", "llm_model"})

    assert out == {"llm_provider": "openai", "llm_model": "gpt-4"}
    assert "secret" not in out


@pytest.mark.asyncio
async def test_get_llm_settings_full_blob_when_no_filter():
    from db_layer import config_store as store

    result = MagicMock()
    result.fetchone.return_value = ({"llm_provider": "openai", "extra": 1},)
    cm, _ = _session_cm(execute_result=result)

    with patch.object(store, "get_async_session", return_value=cm):
        out = await store.get_llm_settings()

    assert out == {"llm_provider": "openai", "extra": 1}


@pytest.mark.asyncio
async def test_upsert_llm_settings_commits():
    from db_layer import config_store as store

    result = MagicMock()
    cm, session = _session_cm(execute_result=result)

    with patch.object(store, "get_async_session", return_value=cm):
        await store.upsert_llm_settings({"llm_provider": "anthropic"})

    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()
