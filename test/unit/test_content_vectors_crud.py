"""Unit tests for content vectors CRUD operations."""

import datetime
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from db_layer.content_vectors_store import (
    search_content_vectors,
    list_content_vectors,
    delete_content_vector,
)
from db_layer.search_content_vectors_store import search_search_content_vectors_filtered


@pytest.mark.asyncio
async def test_search_includes_id():
    """Verify search_content_vectors includes the id key in the returned results."""
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    mock_row = MagicMock()
    mock_row.id = 101
    mock_row.collection = "global_memory"
    mock_row.content_text = "test query result"
    mock_row.meta = {"key": "value"}
    mock_row.similarity = 0.95

    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row]
    mock_session.execute.return_value = mock_result

    with patch("db_layer.embeddings.search_engine.get_async_session", return_value=mock_session_ctx), \
         patch("core.llm_config_service.resolve_tool_embedding", AsyncMock(return_value={})), \
         patch("db_layer.embeddings.search_engine.model_id_for", return_value="mock-model"), \
         patch("db_layer.embeddings.search_engine.embed_query_with", AsyncMock(return_value=[0.1])), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", False), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {}):

        res = await search_content_vectors(
            query="test query",
            collection="global_memory",
            tenant_id=1,
        )

        assert len(res) == 1
        assert res[0] == {
            "id": 101,
            "collection": "global_memory",
            "content_text": "test query result",
            "metadata": {"key": "value"},
            "similarity": 0.95,
        }


@pytest.mark.asyncio
async def test_list_content_vectors():
    """Verify list_content_vectors lists content vectors without invoking embedding functions."""
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    now = datetime.datetime(2026, 7, 5, 20, 0, 0, tzinfo=datetime.timezone.utc)
    mock_row1 = MagicMock()
    mock_row1.id = 1
    mock_row1.content_text = "content 1"
    mock_row1.meta = {"info": "first"}
    mock_row1.created_at = now

    mock_row2 = MagicMock()
    mock_row2.id = 2
    mock_row2.content_text = "content 2"
    mock_row2.meta = None
    mock_row2.created_at = None

    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row1, mock_row2]
    mock_session.execute.return_value = mock_result

    with patch("db_layer.search_content_vectors_store.get_async_session", return_value=mock_session_ctx):
        res = await list_content_vectors(collection="docs", limit=10, offset=2, tenant_id=1)

        assert res == [
            {
                "id": 1,
                "content_text": "content 1",
                "metadata": {"info": "first"},
                "created_at": "2026-07-05T20:00:00+00:00",
            },
            {
                "id": 2,
                "content_text": "content 2",
                "metadata": None,
                "created_at": None,
            },
        ]
        
        mock_session.execute.assert_called_once()
        stmt = mock_session.execute.call_args[0][0]
        assert stmt._limit == 10
        assert stmt._offset == 2
        
        stmt_str = str(stmt)
        assert "search_content_vectors.collection = " in stmt_str
        assert "search_content_vectors.created_at DESC" in stmt_str


@pytest.mark.asyncio
async def test_delete_content_vector_found():
    """Verify delete_content_vector returns True when a row is deleted and commits session."""
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_session.execute.return_value = mock_result

    with patch("db_layer.search_content_vectors_store.get_async_session", return_value=mock_session_ctx):
        res = await delete_content_vector(row_id=42, tenant_id=1)

        assert res is True
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

        stmt = mock_session.execute.call_args[0][0]
        stmt_str = str(stmt)
        assert "DELETE FROM search_content_vectors" in stmt_str
        assert "search_content_vectors.id = " in stmt_str


@pytest.mark.asyncio
async def test_delete_content_vector_not_found():
    """Verify delete_content_vector returns False when no row is deleted."""
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    mock_result = MagicMock()
    mock_result.rowcount = 0
    mock_session.execute.return_value = mock_result

    with patch("db_layer.search_content_vectors_store.get_async_session", return_value=mock_session_ctx):
        res = await delete_content_vector(row_id=42, tenant_id=1)

        assert res is False
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_delete_content_vector_collection_guard():
    """Verify delete_content_vector with collection filter executes and respects rowcount."""
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    mock_result = MagicMock()
    mock_result.rowcount = 0
    mock_session.execute.return_value = mock_result

    with patch("db_layer.search_content_vectors_store.get_async_session", return_value=mock_session_ctx):
        res = await delete_content_vector(row_id=42, collection="global_memory", tenant_id=1)

        assert res is False
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

        stmt = mock_session.execute.call_args[0][0]
        stmt_str = str(stmt)
        assert "DELETE FROM search_content_vectors" in stmt_str
        assert "search_content_vectors.id = " in stmt_str
        assert "search_content_vectors.collection = " in stmt_str


@pytest.mark.asyncio
async def test_search_filtered_builds_jsonb_predicate_per_meta_key():
    """meta_equals keys must reach the query as bound CAST(meta AS JSONB) ->>
    predicates -- SQL-level filtering, not the Python-side over-fetch
    core.memory.service.search_memory uses."""
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute.return_value = mock_result

    with patch("db_layer.embeddings.search_engine.get_async_session", return_value=mock_session_ctx), \
         patch("core.llm_config_service.resolve_tool_embedding", AsyncMock(return_value={})), \
         patch("db_layer.embeddings.search_engine.model_id_for", return_value="mock-model"), \
         patch("db_layer.embeddings.search_engine.embed_query_with", AsyncMock(return_value=[0.1])), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", False), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {}):
        await search_search_content_vectors_filtered(
            "redesign for platform engineer",
            "portfolio_plugin__layouts",
            tenant_id=1,
            meta_equals={"goal_class": "redesign", "passed": "true"},
        )

    mock_session.execute.assert_called_once()
    stmt = mock_session.execute.call_args[0][0]
    stmt_str = str(stmt)
    assert "CAST(meta AS JSONB) ->> :mk_0" in stmt_str
    assert "CAST(meta AS JSONB) ->> :mk_1" in stmt_str
    compiled = stmt.compile()
    params = compiled.params
    bound = {k: v for k, v in params.items() if k.startswith("mk_") or k.startswith("mv_")}
    assert set(bound.values()) >= {"goal_class", "redesign", "passed", "true"}


@pytest.mark.asyncio
async def test_search_filtered_rejects_bad_meta_keys():
    with pytest.raises(ValueError):
        await search_search_content_vectors_filtered(
            "q", "portfolio_plugin__layouts", tenant_id=1,
            meta_equals={"bad key; DROP TABLE": "x"},
        )


@pytest.mark.asyncio
async def test_search_filtered_no_meta_equals_is_plain_search():
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute.return_value = mock_result

    with patch("db_layer.embeddings.search_engine.get_async_session", return_value=mock_session_ctx), \
         patch("core.llm_config_service.resolve_tool_embedding", AsyncMock(return_value={})), \
         patch("db_layer.embeddings.search_engine.model_id_for", return_value="mock-model"), \
         patch("db_layer.embeddings.search_engine.embed_query_with", AsyncMock(return_value=[0.1])), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", False), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {}):
        res = await search_search_content_vectors_filtered(
            "q", "portfolio_plugin__layouts", tenant_id=1,
        )
    assert res == []
    stmt = mock_session.execute.call_args[0][0]
    assert "CAST(meta AS JSONB)" not in str(stmt)
