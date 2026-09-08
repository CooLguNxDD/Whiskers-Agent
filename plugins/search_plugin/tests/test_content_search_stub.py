"""Unit tests for search plugin content tools and content vectors store."""

import hashlib
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from sqlalchemy.exc import IntegrityError

from plugins.search_plugin.MCPTools.content_search_tools import (
    semantic_index,
    semantic_search,
)
from db_layer.search_content_vectors_store import (
    add_content_vector,
    search_content_vectors,
)


@pytest.mark.asyncio
async def test_semantic_index_validation():
    """Verify semantic_index tool validates missing or empty content/collection."""
    res1 = await semantic_index(content="  ", collection="test")
    assert res1 == {
        "status": "error",
        "error": "missing_required_fields",
        "missing_fields": ["content"],
    }

    res2 = await semantic_index(content="text", collection=" ")
    assert res2 == {
        "status": "error",
        "error": "missing_required_fields",
        "missing_fields": ["collection"],
    }


@pytest.mark.asyncio
async def test_semantic_index_wired():
    """semantic_index delegates to add_content_vector with current tenant_id."""
    mock_add = AsyncMock(return_value={"status": "ok", "id": 99})
    with (
        patch(
            "plugins.search_plugin.MCPTools.content_search_tools.add_search_content_vector",
            mock_add,
        ),
        patch(
            "plugins.search_plugin.MCPTools.content_search_tools.current_tenant_id"
        ) as mock_tid,
    ):
        mock_tid.get.return_value = 5
        res = await semantic_index(
            content="some content",
            collection="namespace",
            metadata={"k": "v"},
        )

    assert res == {"status": "ok", "id": 99}
    mock_add.assert_awaited_once_with(
        collection="namespace",
        content_text="some content",
        metadata={"k": "v"},
        tenant_id=5,
    )


@pytest.mark.asyncio
async def test_semantic_index_store_error():
    """Store exceptions map to index_failed envelope."""
    mock_add = AsyncMock(side_effect=RuntimeError("embed boom"))
    with (
        patch(
            "plugins.search_plugin.MCPTools.content_search_tools.add_search_content_vector",
            mock_add,
        ),
        patch(
            "plugins.search_plugin.MCPTools.content_search_tools.current_tenant_id"
        ) as mock_tid,
    ):
        mock_tid.get.return_value = 1
        res = await semantic_index(content="x", collection="c")

    assert res["status"] == "error"
    assert res["error"] == "index_failed"
    assert res["message"] == "An internal error occurred during the index operation."
    assert "embed boom" not in res["message"]


@pytest.mark.asyncio
async def test_semantic_search_validation():
    """Verify semantic_search tool validates missing or empty query/collection."""
    res1 = await semantic_search(query="  ", collection="test")
    assert res1 == {
        "status": "error",
        "error": "missing_required_fields",
        "missing_fields": ["query"],
    }

    res2 = await semantic_search(query="text", collection=" ")
    assert res2 == {
        "status": "error",
        "error": "missing_required_fields",
        "missing_fields": ["collection"],
    }


@pytest.mark.asyncio
async def test_semantic_search_wired():
    """semantic_search wraps store results in ok envelope with tenant_id."""
    hits = [
        {
            "id": 1,
            "collection": "namespace",
            "content_text": "hit",
            "metadata": {},
            "similarity": 0.9,
        }
    ]
    mock_search = AsyncMock(return_value=hits)
    with (
        patch(
            "plugins.search_plugin.MCPTools.content_search_tools.search_search_content_vectors",
            mock_search,
        ),
        patch(
            "plugins.search_plugin.MCPTools.content_search_tools.current_tenant_id"
        ) as mock_tid,
    ):
        mock_tid.get.return_value = 5
        res = await semantic_search(query="q", collection="namespace", top_k=3)

    assert res == {
        "status": "ok",
        "collection": "namespace",
        "count": 1,
        "results": hits,
    }
    mock_search.assert_awaited_once_with(
        query="q",
        collection="namespace",
        top_k=3,
        tenant_id=5,
    )


@pytest.mark.asyncio
async def test_add_content_vector_success():
    """Verify add_content_vector processes input, embeds, and saves to database."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    def mock_add(row):
        row.id = 123

    mock_session.add.side_effect = mock_add

    with patch("db_layer.search_content_vectors_store.get_async_session", return_value=mock_session_ctx), \
         patch("core.llm_config_service.resolve_tool_embedding", AsyncMock(return_value={"mock": True})) as mock_resolve, \
         patch("db_layer.embeddings.embeddings_core.model_id_for", return_value="mock-model") as mock_model_id, \
         patch("db_layer.embeddings.embeddings_core.embed_query_with", AsyncMock(return_value=[0.1, 0.2, 0.3])) as mock_embed:

        res = await add_content_vector(
            collection="my_collection",
            content_text="Hello, Whiskers Agent!",
            metadata={"source": "pytest"},
            tenant_id=2,
        )

        assert res == {"status": "ok", "id": 123}

        mock_resolve.assert_called_once_with("plugins.search_plugin", "semantic_index")
        mock_model_id.assert_called_once()
        mock_embed.assert_called_once_with({"mock": True}, "Hello, Whiskers Agent!")

        mock_session.add.assert_called_once()
        added_obj = mock_session.add.call_args[0][0]
        assert added_obj.collection == "my_collection"
        assert added_obj.content_text == "Hello, Whiskers Agent!"
        assert added_obj.meta == {"source": "pytest"}
        assert added_obj.model == "mock-model"
        assert added_obj.embedding == [0.1, 0.2, 0.3]
        assert added_obj.tenant_id == 2

        expected_hash = hashlib.sha256("Hello, Whiskers Agent!".encode("utf-8")).hexdigest()
        assert added_obj.content_hash == expected_hash

        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_add_content_vector_requires_tenant_id():
    """tenant_id is required — omitting it raises TypeError."""
    with pytest.raises(TypeError):
        await add_content_vector(collection="c", content_text="t")


@pytest.mark.asyncio
async def test_add_content_vector_integrity_error():
    """Verify add_content_vector handles unique constraint violations gracefully."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit.side_effect = IntegrityError(None, None, None)
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("db_layer.search_content_vectors_store.get_async_session", return_value=mock_session_ctx), \
         patch("core.llm_config_service.resolve_tool_embedding", AsyncMock(return_value={})), \
         patch("db_layer.embeddings.embeddings_core.model_id_for", return_value="mock-model"), \
         patch("db_layer.embeddings.embeddings_core.embed_query_with", AsyncMock(return_value=[0.1])):

        res = await add_content_vector(
            collection="my_collection",
            content_text="Hello, Whiskers Agent!",
            tenant_id=1,
        )

        assert res == {"status": "ok", "deduped": True}
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_search_content_vectors():
    """Verify search_content_vectors (dense-only) builds query, executes, and maps rows.

    search_content_vectors now runs through the shared search engine
    (db_layer/embeddings/search_engine.py) — patches target the engine's own
    module-level bindings, not search_content_vectors_store's, since the
    engine imported get_async_session/embed_query_with/model_id_for by value.
    Hybrid is forced off so this stays a single dense execute() call, same as
    the pre-engine behavior this test originally asserted.
    """
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    mock_row = MagicMock()
    mock_row.id = 42
    mock_row.collection = "col1"
    mock_row.content_text = "some match text"
    mock_row.meta = {"tag": "val"}
    mock_row.similarity = 0.89

    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row]
    mock_session.execute.return_value = mock_result

    with patch("db_layer.embeddings.search_engine.get_async_session", return_value=mock_session_ctx), \
         patch("core.llm_config_service.resolve_tool_embedding", AsyncMock(return_value={})) as mock_resolve, \
         patch("db_layer.embeddings.search_engine.model_id_for", return_value="mock-model") as mock_model_id, \
         patch("db_layer.embeddings.search_engine.embed_query_with", AsyncMock(return_value=[0.1])) as mock_embed, \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", False), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {}):

        res = await search_content_vectors(
            query="find me something",
            collection="col1",
            top_k=3,
            tenant_id=2,
        )

        assert len(res) == 1
        assert res[0] == {
            "id": 42,
            "collection": "col1",
            "content_text": "some match text",
            "metadata": {"tag": "val"},
            "similarity": 0.89,
        }

        mock_resolve.assert_called_once_with("plugins.search_plugin", "semantic_index")
        mock_model_id.assert_called_once()
        mock_embed.assert_called_once_with({}, "find me something")
        mock_session.execute.assert_called_once()


def test_content_sync_worker_register_disabled_by_default():
    """content_sync_worker registers under its name; enabled_check is falsy by default."""
    from core_graph.worker.worker_registry import WorkerRegistry
    from core_graph.worker import content_sync_worker

    registry = WorkerRegistry()
    content_sync_worker.register(registry)
    spec = registry.get("content_sync_worker")
    assert spec is not None
    check = spec.enabled_check
    assert check is not None
    with patch("utils.server_config.SCHEDULED_JOBS_CONFIG", {}):
        assert check() is False


@pytest.mark.asyncio
async def test_content_sync_worker_stop_event_ready_before_loop():
    """run() creates the stop event before the loop so stop() can signal immediately."""
    from core_graph.worker import content_sync_worker

    worker = content_sync_worker._WORKER
    task = content_sync_worker.run(idle_poll=0.05)
    try:
        assert worker.stop_event is not None
        assert not worker.stop_event.is_set()
        await content_sync_worker.stop()
        assert task.done()
        assert worker.task is None
        assert worker.stop_event is None
    finally:
        if worker.task is not None:
            await content_sync_worker.stop()
