import core.llm.pool_manager as pool_manager
import core.llm.vault_registry as vault_registry
import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
from core.llm_config_service import resolve_chat, resolve_embedding, config_version, add_pool_entry
import core.llm_config_service as llm_config_service
from core.llm_config_service import get_active_map, set_active, get_active, delete_pool_entry, set_pool_entry_active


@pytest.mark.asyncio
async def test_resolve_chat_env_fallback():
    # When DB is not available, should fall back to environment variables.
    with patch("core.llm.pool_manager._db_available", return_value=False), \
         patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-2.5-pro"}):
        
        cfg = await resolve_chat()
        assert cfg["provider"] == "gemini"
        assert cfg["model"] == "gemini-2.5-pro"
        assert cfg["source"] == "env"


@pytest.mark.asyncio
async def test_resolve_embedding_env_fallback():
    # Embedding fallback defaults to text-embedding-3-small for OpenAI/Anthropic
    with patch("core.llm.pool_manager._db_available", return_value=False), \
         patch.dict(os.environ, {
             "EMBED_PROVIDER": "openai",
             "EMBED_MODEL": "",
             "EMBED_DIMENSIONS": "",
             "EMBED_API_KEY": "",
             "EMBED_BASE_URL": ""
         }):
        
        cfg = await resolve_embedding()
        assert cfg["provider"] == "openai"
        assert cfg["model"] == "text-embedding-3-small"
        assert cfg["dimensions"] == 1536
        assert cfg["source"] == "env"


@pytest.mark.asyncio
async def test_config_version_changes_on_update():
    with patch("core.llm.pool_manager._db_available", return_value=False):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-4o"}):
            v1 = await config_version()
            
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-3.5-turbo"}):
            v2 = await config_version()
            
        assert v1 != v2


@pytest.mark.asyncio
async def test_add_pool_entry_bad_provider_rejected():
    with pytest.raises(ValueError) as exc_info:
        await add_pool_entry(
            name="Bad Model",
            provider="invalid-provider-123",
            model="some-model"
        )
    assert "Unsupported provider" in str(exc_info.value)


@pytest.mark.asyncio
async def test_resolve_chat_highest_strength():
    # When DB is available but no active override is set, should pick highest strength model.
    mock_row = MagicMock()
    mock_row.id = "uuid-high-strength"
    mock_row.provider = "anthropic"
    mock_row.model = "claude-3-opus"
    mock_row.dimensions = None
    mock_row.base_url = "http://anthropic-api"
    mock_row.strength = 100.0
    mock_row.api_key = "decrypted-key-opus"

    mock_execute_res = MagicMock()
    mock_execute_res.fetchone.return_value = mock_row

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_execute_res

    class AsyncContextManagerMock:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("core.llm.pool_manager._db_available", return_value=True), \
         patch("core.llm_config_service.get_active", new_callable=AsyncMock, return_value=None), \
         patch("core.llm.pool_manager.get_async_session", return_value=AsyncContextManagerMock()), patch("core.llm_config_service.get_async_session", return_value=AsyncContextManagerMock()):

        cfg = await resolve_chat()
        assert cfg["provider"] == "anthropic"
        assert cfg["model"] == "claude-3-opus"
        assert cfg["api_key"] == "decrypted-key-opus"
        assert cfg["base_url"] == "http://anthropic-api"
        assert cfg["source"] == "pool"


@pytest.mark.asyncio
async def test_resolve_embedding_highest_strength():
    # When DB is available but no active override is set, should pick highest strength embedding model.
    mock_row = MagicMock()
    mock_row.id = "uuid-high-strength-emb"
    mock_row.provider = "openai"
    mock_row.model = "text-embedding-3-large"
    mock_row.dimensions = 3072
    mock_row.base_url = "http://openai-api"
    mock_row.strength = 50.0
    mock_row.api_key = "decrypted-key-emb"

    mock_execute_res = MagicMock()
    mock_execute_res.fetchone.return_value = mock_row

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_execute_res

    class AsyncContextManagerMock:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("core.llm.pool_manager._db_available", return_value=True), \
         patch("core.llm_config_service.get_active", new_callable=AsyncMock, return_value=None), \
         patch("core.llm.pool_manager.get_async_session", return_value=AsyncContextManagerMock()), patch("core.llm_config_service.get_async_session", return_value=AsyncContextManagerMock()):

        cfg = await resolve_embedding()
        assert cfg["provider"] == "openai"
        assert cfg["model"] == "text-embedding-3-large"
        assert cfg["dimensions"] == 3072
        assert cfg["api_key"] == "decrypted-key-emb"
        assert cfg["base_url"] == "http://openai-api"
        assert cfg["source"] == "pool"


@pytest.mark.asyncio
async def test_resolve_route_embedding_env_fallback():
    from core.llm_config_service import resolve_route_embedding
    with patch("core.llm.pool_manager._db_available", return_value=False), \
         patch.dict(os.environ, {
             "EMBED_PROVIDER": "gemini",
             "EMBED_MODEL": "gemini-embedding-001",
             "EMBED_DIMENSIONS": "1536",
         }):
        cfg = await resolve_route_embedding()
        assert cfg["provider"] == "gemini"
        assert cfg["model"] == "gemini-embedding-001"
        assert cfg["dimensions"] == 1536
        assert cfg["source"] == "env"


@pytest.mark.asyncio
async def test_resolve_tool_embedding_env_fallback():
    from core.llm_config_service import resolve_tool_embedding
    with patch("core.llm.pool_manager._db_available", return_value=False), \
         patch.dict(os.environ, {
             "EMBED_PROVIDER": "openai",
             "EMBED_MODEL": "text-embedding-3-small",
             "EMBED_DIMENSIONS": "1536",
         }):
        cfg = await resolve_tool_embedding("core", "upsert_note_embedding")
        assert cfg["provider"] == "openai"
        assert cfg["model"] == "text-embedding-3-small"
        assert cfg["dimensions"] == 1536
        assert cfg["source"] == "env"


def test_model_id_for():
    from db_layer.embeddings.embeddings_core import model_id_for
    sel = {
        "provider": "openai",
        "model": "text-embedding-3-large",
        "dimensions": 3072
    }
    assert model_id_for(sel) == "openai:text-embedding-3-large:3072"


@pytest.mark.asyncio
async def test_resolve_core_chat_fallback():
    from core.llm_config_service import resolve_core_chat
    # When DB is not available, should fall back to environment variables.
    with patch("core.llm.pool_manager._db_available", return_value=False), \
         patch.dict(os.environ, {"LLM_PROVIDER": "anthropic", "LLM_MODEL": "claude-3-opus"}):
        cfg = await resolve_core_chat()
        assert cfg["provider"] == "anthropic"
        assert cfg["model"] == "claude-3-opus"
        assert cfg["source"] == "env"


@pytest.mark.asyncio
async def test_resolve_step_llm_fallback():
    from core.llm_config_service import resolve_step_llm
    # When step is None, should fall back to core LLM
    with patch("core.llm_config_service.get_graph_core_llm") as mock_core:
        mock_core.return_value = "mocked-core-llm"
        res = await resolve_step_llm(None)
        assert res == "mocked-core-llm"


@pytest.mark.asyncio
async def test_resolve_step_llm_active_inactive():
    from core.llm_config_service import resolve_step_llm
    
    mock_row_active = MagicMock()
    mock_row_active.name = "active-model"
    mock_row_active.provider = "openai"
    mock_row_active.model = "gpt-4o"
    mock_row_active.dimensions = None
    mock_row_active.base_url = None
    mock_row_active.strength = 10.0
    mock_row_active.is_active = True
    mock_row_active.api_key = "key1"
    
    mock_row_inactive = MagicMock()
    mock_row_inactive.name = "inactive-model"
    mock_row_inactive.provider = "openai"
    mock_row_inactive.model = "gpt-3.5"
    mock_row_inactive.dimensions = None
    mock_row_inactive.base_url = None
    mock_row_inactive.strength = 5.0
    mock_row_inactive.is_active = False
    mock_row_inactive.api_key = "key2"
    
    mock_execute_res = MagicMock()
    mock_execute_res.all.return_value = [mock_row_active, mock_row_inactive]
    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_execute_res
    
    class AsyncContextManagerMock:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("core.llm.pool_manager._db_available", return_value=True), \
         patch("core.llm.pool_manager.get_async_session", return_value=AsyncContextManagerMock()), patch("core.llm_config_service.get_async_session", return_value=AsyncContextManagerMock()), \
         patch("core.llm_config_service.get_chat_llm", return_value="resolved-step-llm") as mock_get_chat:
         
        # Resolving for active-model should succeed
        res = await resolve_step_llm({"model": "active-model"})
        assert res == "resolved-step-llm"
        
        # Resolving for inactive-model should match nearest active (which is active-model because it's the only active one)
        res_inactive = await resolve_step_llm({"model": "inactive-model"})
        assert res_inactive == "resolved-step-llm"





def _make_session_mock(value):
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, idx: value
    mock_execute_res = MagicMock()
    mock_execute_res.fetchone.return_value = mock_row

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_execute_res

    class AsyncContextManagerMock:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    return AsyncContextManagerMock()


@pytest.fixture(autouse=True)
def _reset_active_map_cache():
    # Each test starts with a cold cache so TTL assertions are deterministic.
    pool_manager._ACTIVE_MAP_CACHE["value"] = {}
    pool_manager._ACTIVE_MAP_CACHE["expires_at"] = 0.0
    vault_registry._ENTRY_CACHE.clear()
    yield
    pool_manager._ACTIVE_MAP_CACHE["value"] = {}
    pool_manager._ACTIVE_MAP_CACHE["expires_at"] = 0.0
    vault_registry._ENTRY_CACHE.clear()


@pytest.mark.asyncio
async def test_get_active_map_hits_db_once_then_serves_from_cache():
    session_ctx = _make_session_mock({"chat": "model-a"})
    with patch("core.llm.pool_manager._db_available", return_value=True), \
         patch("core.llm.pool_manager.get_async_session", return_value=session_ctx) as mock_get_session:

        first = await get_active_map()
        second = await get_active_map()

        assert first == {"chat": "model-a"}
        assert second == {"chat": "model-a"}
        # Second call must be served from the in-memory cache, not the DB.
        assert mock_get_session.call_count == 1


@pytest.mark.asyncio
async def test_get_active_map_refetches_after_ttl_expires():
    session_ctx = _make_session_mock({"chat": "model-a"})
    with patch("core.llm.pool_manager._db_available", return_value=True), \
         patch("core.llm.pool_manager.get_async_session", return_value=session_ctx) as mock_get_session:

        await get_active_map()
        # Simulate TTL expiry by forcing the cache to be stale.
        pool_manager._ACTIVE_MAP_CACHE["expires_at"] = 0.0
        await get_active_map()

        assert mock_get_session.call_count == 2


@pytest.mark.asyncio
async def test_get_active_map_returns_copy_not_shared_reference():
    session_ctx = _make_session_mock({"chat": "model-a"})
    with patch("core.llm.pool_manager._db_available", return_value=True), \
         patch("core.llm.pool_manager.get_async_session", return_value=session_ctx):

        result = await get_active_map()
        result["chat"] = "mutated"

        # Mutating the caller's copy must not poison the shared cache.
        second = await get_active_map()
        assert second == {"chat": "model-a"}


@pytest.mark.asyncio
async def test_set_active_invalidates_cache():
    session_ctx = _make_session_mock({"chat": "model-a"})
    with patch("core.llm.pool_manager._db_available", return_value=True), \
         patch("core.llm.pool_manager.get_async_session", return_value=session_ctx) as mock_get_session:

        await get_active_map()
        assert mock_get_session.call_count == 1

        await set_active("embedding", "model-b")

        # set_active's internal get_active_map() read is served from cache (still valid),
        # but the cache must be marked stale afterwards so the next read re-hits the DB.
        await get_active_map()
        assert mock_get_session.call_count == 3


def _make_entry_row_session_mock(row):
    mock_execute_res = MagicMock()
    mock_execute_res.fetchone.return_value = row

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_execute_res

    class AsyncContextManagerMock:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    return AsyncContextManagerMock()


def _active_entry_row(entry_id="entry-1"):
    row = MagicMock()
    row.id = entry_id
    row.provider = "openai"
    row.model = "gpt-4o"
    row.dimensions = None
    row.base_url = None
    row.strength = 1.0
    row.is_active = True
    row.api_key = "decrypted-key"
    return row


@pytest.mark.asyncio
async def test_get_entry_with_token_hits_db_once_then_serves_from_cache():
    from core.llm.vault_registry import _get_entry_with_token
    session_ctx = _make_entry_row_session_mock(_active_entry_row("entry-1"))

    with patch("core.llm.vault_registry.get_async_session", return_value=session_ctx) as mock_get_session:
        first = await _get_entry_with_token("entry-1")
        second = await _get_entry_with_token("entry-1")

        assert first["provider"] == "openai"
        assert second["provider"] == "openai"
        assert mock_get_session.call_count == 1


@pytest.mark.asyncio
async def test_get_entry_with_token_returns_copy_not_shared_reference():
    from core.llm.vault_registry import _get_entry_with_token
    session_ctx = _make_entry_row_session_mock(_active_entry_row("entry-1"))

    with patch("core.llm.vault_registry.get_async_session", return_value=session_ctx):
        first = await _get_entry_with_token("entry-1")
        first["provider"] = "mutated"

        second = await _get_entry_with_token("entry-1")
        assert second["provider"] == "openai"


@pytest.mark.asyncio
async def test_get_entry_with_token_caches_negative_lookup():
    from core.llm.vault_registry import _get_entry_with_token
    session_ctx = _make_entry_row_session_mock(None)

    with patch("core.llm.vault_registry.get_async_session", return_value=session_ctx) as mock_get_session:
        first = await _get_entry_with_token("missing-entry")
        second = await _get_entry_with_token("missing-entry")

        assert first is None
        assert second is None
        assert mock_get_session.call_count == 1


@pytest.mark.asyncio
async def test_delete_pool_entry_invalidates_entry_cache():
    from core.llm.vault_registry import _get_entry_with_token
    session_ctx = _make_entry_row_session_mock(_active_entry_row("entry-1"))

    with patch("core.llm.vault_registry.get_async_session", return_value=session_ctx) as mock_get_session, patch("core.llm.pool_manager.get_async_session", return_value=session_ctx):
        await _get_entry_with_token("entry-1")
        assert mock_get_session.call_count == 1

        await delete_pool_entry("entry-1")
        await _get_entry_with_token("entry-1")

        assert mock_get_session.call_count == 2


@pytest.mark.asyncio
async def test_set_pool_entry_active_invalidates_entry_cache():
    from core.llm.vault_registry import _get_entry_with_token
    session_ctx = _make_entry_row_session_mock(_active_entry_row("entry-1"))

    with patch("core.llm.vault_registry.get_async_session", return_value=session_ctx) as mock_get_session, patch("core.llm.pool_manager.get_async_session", return_value=session_ctx):
        await _get_entry_with_token("entry-1")
        assert mock_get_session.call_count == 1

        await set_pool_entry_active("entry-1", False)
        await _get_entry_with_token("entry-1")

        assert mock_get_session.call_count == 2


@pytest.mark.asyncio
async def test_get_active_uses_cached_entry_across_calls():
    session_ctx_map = _make_session_mock({"chat": "entry-1"})
    session_ctx_entry = _make_entry_row_session_mock(_active_entry_row("entry-1"))

    call_log = []

    def _get_session_side_effect():
        call_log.append(True)
        # First call resolves the active map, subsequent calls resolve the entry.
        return session_ctx_map if len(call_log) == 1 else session_ctx_entry

    with patch("core.llm.pool_manager._db_available", return_value=True), \
         patch("core.llm.pool_manager.get_async_session", side_effect=_get_session_side_effect), \
         patch("core.llm.vault_registry.get_async_session", side_effect=_get_session_side_effect):

        first = await get_active("chat")
        second = await get_active("chat")

        assert first["provider"] == "openai"
        assert second["provider"] == "openai"
        # 1 DB hit for the active map (cached after) + 1 DB hit for the entry (cached after).
        assert len(call_log) == 2
