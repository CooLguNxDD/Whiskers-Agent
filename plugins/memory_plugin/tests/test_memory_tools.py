"""Unit tests for memory_plugin MCP tools (core.memory façade)."""

from unittest.mock import AsyncMock, patch

import pytest

from plugins.memory_plugin.MCPTools.memory_tools import (
    delete_memory,
    list_memories,
    save_memory,
    search_anti_patterns,
    search_memory,
    search_plan_recipes,
)

# Plugin tools call core.memory.service → memory_content_vectors_store.
_STORE = "core.memory.service"


@pytest.mark.asyncio
async def test_save_memory_blank_content():
    """Verify save_memory returns missing_required_fields when content is blank."""
    with patch(f"{_STORE}.add_memory_content_vector", new_callable=AsyncMock) as mock_add:
        res = await save_memory(content="   ", tags=["tag1"], tier="global")
        assert res["status"] == "error"
        assert res["error"] == "missing_required_fields"
        assert "content" in res["missing_fields"]
        mock_add.assert_not_called()


@pytest.mark.asyncio
async def test_save_memory_ok(monkeypatch):
    """Verify save_memory successfully calls add_memory_content_vector with correct metadata."""
    from core.context import current_tenant_id

    # Isolate from suite-order pollution of the ContextVar.
    token = current_tenant_id.set(1)
    mock_response = {"status": "ok", "id": 7}
    try:
        with patch(
            f"{_STORE}.add_memory_content_vector",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_add:
            res = await save_memory(content="test content", tags=["tag1"])
            assert res == mock_response
            mock_add.assert_called_once()
            args, kwargs = mock_add.call_args
            assert args[0] == "global_memory"
            assert args[1] == "test content"
            metadata = kwargs["metadata"]
            assert metadata["tags"] == ["tag1"]
            assert metadata["tier"] == "global"
            assert "saved_at" in metadata
            assert kwargs["tenant_id"] == 1
    finally:
        current_tenant_id.reset(token)


@pytest.mark.asyncio
async def test_save_memory_rejects_plan_collection():
    """Agents cannot write harness plan_recipes via MCP save_memory allowlist."""
    with patch(f"{_STORE}.add_memory_content_vector", new_callable=AsyncMock) as mock_add:
        res = await save_memory(content="x", collection="plan_recipes")
        assert res["status"] == "error"
        assert res["error"] == "collection_not_allowed"
        mock_add.assert_not_called()


@pytest.mark.asyncio
async def test_save_memory_bad_tier_coerced():
    """Verify save_memory coerces invalid tier values to global."""
    mock_response = {"status": "ok", "id": 7}
    with patch(
        f"{_STORE}.add_memory_content_vector",
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_add:
        res = await save_memory(content="x", tier="not-a-tier")
        assert res == mock_response
        metadata = mock_add.call_args.kwargs["metadata"]
        assert metadata["tier"] == "global"


@pytest.mark.asyncio
async def test_search_memory_blank_query():
    """Verify search_memory rejects blank queries."""
    with patch(
        f"{_STORE}.search_memory_content_vectors", new_callable=AsyncMock
    ) as mock_search:
        res = await search_memory(query="  ")
        assert res["status"] == "error"
        mock_search.assert_not_called()


@pytest.mark.asyncio
async def test_search_memory_ok_filters_tag():
    """Verify search_memory filters by tag after vector search."""
    mock_rows = [
        {
            "id": 1,
            "content_text": "alpha",
            "metadata": {"tags": ["a"], "tier": "global"},
            "similarity": 0.9,
        },
        {
            "id": 2,
            "content_text": "beta",
            "metadata": {"tags": ["b"], "tier": "global"},
            "similarity": 0.8,
        },
    ]
    with patch(
        f"{_STORE}.search_memory_content_vectors",
        new_callable=AsyncMock,
        return_value=mock_rows,
    ) as mock_search:
        res = await search_memory(query="q", top_k=5, tag="a")
        assert res["status"] == "ok"
        assert res["count"] == 1
        assert res["memories"][0]["content"] == "alpha"
        mock_search.assert_called_once()


@pytest.mark.asyncio
async def test_search_memory_plan_recipes_collection():
    """Agents may search plan_recipes via search_memory collection param."""
    with patch(
        f"{_STORE}.search_memory_content_vectors",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_search:
        res = await search_memory(query="jules first session", collection="plan_recipes")
        assert res["status"] == "ok"
        mock_search.assert_called_once()
        assert mock_search.call_args.args[1] == "plan_recipes"


@pytest.mark.asyncio
async def test_list_memories_ok():
    """Verify list_memories maps store rows to tool shape."""
    mock_rows = [
        {
            "id": 3,
            "content_text": "note",
            "metadata": {"tags": [], "tier": "global"},
            "created_at": "2020-01-01T00:00:00",
        }
    ]
    with patch(
        f"{_STORE}.list_memory_content_vectors",
        new_callable=AsyncMock,
        return_value=mock_rows,
    ) as mock_list:
        res = await list_memories(limit=10, offset=0)
        assert res["status"] == "ok"
        assert res["count"] == 1
        assert res["memories"][0]["id"] == 3
        mock_list.assert_called_once()


@pytest.mark.asyncio
async def test_delete_memory_ok():
    """Verify delete_memory returns deleted true when store succeeds."""
    with patch(
        f"{_STORE}.delete_memory_content_vector",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_delete:
        res = await delete_memory(memory_id=9)
        assert res == {"status": "ok", "deleted": True}
        mock_delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_memory_not_found():
    """Verify delete_memory returns memory_not_found when store misses."""
    with patch(
        f"{_STORE}.delete_memory_content_vector",
        new_callable=AsyncMock,
        return_value=False,
    ) as mock_delete:
        res = await delete_memory(memory_id=9)
        assert res["status"] == "error"
        assert res["error"] == "memory_not_found"
        mock_delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_memory_rejects_anti_pattern_collection():
    """Agents cannot delete harness anti-pattern rows via MCP."""
    with patch(
        f"{_STORE}.delete_memory_content_vector", new_callable=AsyncMock
    ) as mock_delete:
        res = await delete_memory(memory_id=1, collection="plan_anti_patterns")
        assert res["status"] == "error"
        assert res["error"] == "collection_not_allowed"
        mock_delete.assert_not_called()


@pytest.mark.asyncio
async def test_save_memory_core_instructions_upserts_json():
    """save_memory(collection=core_instructions) upserts global harness JSON."""
    with patch(
        "core.memory.harness_instructions.upsert_instruction",
        new_callable=AsyncMock,
        return_value={"status": "ok", "kind": "harness_instruction", "id": "custom", "count": 1},
    ) as mock_up, patch(
        f"{_STORE}.add_memory_content_vector",
        new_callable=AsyncMock,
        return_value={"status": "ok", "id": 1},
    ):
        res = await save_memory(
            content="Always list before get.",
            collection="core_instructions",
            tags=["rule"],
        )
        assert res["status"] == "ok"
        assert res.get("kind") == "harness_instruction"
        mock_up.assert_called_once()


@pytest.mark.asyncio
async def test_search_plan_recipes_ok():
    """search_plan_recipes wraps core.memory search_plan_recipes."""
    fake = [
        {
            "id": 1,
            "content": "list then get",
            "op_ids": ["juleslist_sessions", "julesget_session"],
            "plugin_ids": ["jules_plugin"],
            "similarity": 0.91,
            "metadata": {},
        }
    ]
    with patch(
        "plugins.memory_plugin.MCPTools.memory_tools.core_search_plan_recipes",
        new_callable=AsyncMock,
        return_value=fake,
    ) as mock_sr:
        res = await search_plan_recipes(query="first jules session", top_k=2)
        assert res["status"] == "ok"
        assert res["count"] == 1
        assert res["recipes"][0]["op_ids"][0] == "juleslist_sessions"
        mock_sr.assert_called_once()


@pytest.mark.asyncio
async def test_search_anti_patterns_blank():
    """search_anti_patterns rejects blank query."""
    res = await search_anti_patterns(query="  ")
    assert res["status"] == "error"
    assert res["error"] == "missing_required_fields"


@pytest.mark.asyncio
async def test_search_anti_patterns_ok():
    """search_anti_patterns wraps core.memory search_anti_patterns."""
    fake = [
        {
            "id": 2,
            "content": "get before list",
            "plan_ops": ["julesget_session", "juleslist_sessions"],
            "outcome": "error",
            "detail": "missing sessionId",
            "similarity": 0.8,
            "metadata": {},
        }
    ]
    with patch(
        "plugins.memory_plugin.MCPTools.memory_tools.core_search_anti_patterns",
        new_callable=AsyncMock,
        return_value=fake,
    ) as mock_sa:
        res = await search_anti_patterns(query="jules session fail")
        assert res["status"] == "ok"
        assert res["count"] == 1
        assert "julesget_session" in res["anti_patterns"][0]["plan_ops"]
        mock_sa.assert_called_once()
