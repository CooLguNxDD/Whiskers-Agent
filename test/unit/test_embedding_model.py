import pytest
import os
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

from core.llm_config_service import resolve_route_embedding, resolve_tool_embedding
from db_layer.embeddings.embeddings_core import model_id_for


@pytest.mark.asyncio
async def test_resolve_tool_embedding_applies_producer_map():
    # A search tool resolves through _PRODUCER_TOOL_MAP so its query is embedded
    # with the model that wrote the vectors. The map ships empty, so the entry is
    # injected here rather than pinned to a specific tool pair.
    with patch("core.llm_config_service._db_available", return_value=True), \
         patch.dict("core.llm_config_service._PRODUCER_TOOL_MAP",
                    {"semantic_search_notes": "upsert_note_embedding"}, clear=False), \
         patch("db_layer.tool_config_store.get_tool_embedding_model", new_callable=AsyncMock) as mock_get:

        mock_get.return_value = None  # No database override

        # Calling search resolves to fallback env selection
        cfg = await resolve_tool_embedding("core", "semantic_search_notes")
        assert cfg["source"] == "env"
        # Verify the call went out for the producer, not search
        mock_get.assert_called_once_with("core", "upsert_note_embedding")


@pytest.mark.asyncio
async def test_resolve_tool_embedding_unmapped_tool_uses_own_name():
    """A tool with no producer mapping looks itself up, not some other tool."""
    with patch("core.llm_config_service._db_available", return_value=True), \
         patch("db_layer.tool_config_store.get_tool_embedding_model", new_callable=AsyncMock) as mock_get:

        mock_get.return_value = None
        await resolve_tool_embedding("core", "upsert_note_embedding")
        mock_get.assert_called_once_with("core", "upsert_note_embedding")


@pytest.mark.asyncio
async def test_hash_baking():
    from db_layer.embeddings.route_document import DOC_SCHEMA_VERSION
    from core_graph.worker.job_producer import compute_effective_hash

    # Verify that eff_hash is computed using route content_hash, route model ID, and DOC_SCHEMA_VERSION
    route_model_id = "openai:text-embedding-3-small:1536"
    base_hash = "my_content_hash"
    
    expected_eff_hash = hashlib.sha256(f"{base_hash}|{route_model_id}|{DOC_SCHEMA_VERSION}".encode()).hexdigest()
    eff_hash = compute_effective_hash(base_hash, route_model_id)

    assert eff_hash == expected_eff_hash


@pytest.mark.asyncio
async def test_worker_model_grouping():
    # Verify process_batch groups jobs by model_id and invokes embed_documents_with once
    # per group. Both jobs take the default (route) branch; the resolver returns a
    # different model per call, which is the only thing grouping keys on. The other
    # branch is plugin-owned (upsert_unity_world_vector) and is covered by that
    # plugin's own tests — test/ must not patch plugin code
    # (see test_plugin_test_boundary.py).
    jobs = [
        {
            "id": 1,
            "plugin_id": "core",
            "operation_id": "some_operation_id",
            "content_hash": "hash1",
            "payload": {"description": "route content"}
        },
        {
            "id": 2,
            "plugin_id": "core",
            "operation_id": "other_operation_id",
            "content_hash": "hash2",
            "payload": {"description": "other route content"}
        }
    ]

    # Job 1 resolves to model A, job 2 to model B — two groups, two embed calls.
    route_models = [
        {"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536},
        {"provider": "gemini", "model": "gemini-embedding-001", "dimensions": 768},
    ]

    async def mock_resolve_route():
        return route_models.pop(0)

    with patch("core.llm_config_service.resolve_route_embedding", new_callable=AsyncMock, side_effect=mock_resolve_route), \
         patch("db_layer.embeddings.embeddings_core.embed_documents_with", new_callable=AsyncMock) as mock_embed_docs, \
         patch("core_graph.worker.embedding_worker._mark_failed", new_callable=AsyncMock) as mock_failed, \
         patch("core_graph.worker.embedding_worker.get_async_session") as mock_session_ctx:

        # Mock the embed batch results
        mock_embed_docs.side_effect = [
            [[0.1] * 1536], # mock vector for model A (route)
            [[0.2] * 768]   # mock vector for model B
        ]

        # Mock DB session execute
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session

        from core_graph.worker.embedding_worker import process_batch
        await process_batch(jobs)

        # Assert embed_documents_with was called twice (once for each model group)
        assert mock_embed_docs.call_count == 2

        # Verify titles were passed correctly
        call1_kwargs = mock_embed_docs.call_args_list[0].kwargs
        call2_kwargs = mock_embed_docs.call_args_list[1].kwargs
        assert "titles" in call1_kwargs
        assert "titles" in call2_kwargs

        all_titles = call1_kwargs["titles"] + call2_kwargs["titles"]
        assert sorted(all_titles) == sorted(["some_operation_id", "other_operation_id"])

        # Assert no jobs were marked failed
        mock_failed.assert_not_called()
        # Assert DB session executed updates and committed
        assert mock_session.commit.call_count == 1
