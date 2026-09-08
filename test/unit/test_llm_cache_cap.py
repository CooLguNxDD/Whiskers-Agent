import pytest
from unittest.mock import patch, MagicMock
from core.llm_provider_management.factory import get_chat_llm, _llm_cache, _LLM_CACHE_MAX
from core.llm_provider_management import LLMProvider

def test_llm_cache_cap_fifo_eviction():
    # Clear the global cache to start with a clean state
    _llm_cache.clear()

    try:
        # Patch _make_llm so it doesn't make real API calls
        with patch("core.llm_provider_management.factory.make_llm") as mock_make_llm:
            mock_make_llm.return_value = MagicMock()

            # Call get_chat_llm with _LLM_CACHE_MAX + 10 distinct model names
            num_calls = _LLM_CACHE_MAX + 10
            for i in range(num_calls):
                get_chat_llm(LLMProvider.OPENAI, f"model-{i}")

            # Verify the cache size does not exceed the maximum limit
            assert len(_llm_cache) <= _LLM_CACHE_MAX
            assert len(_llm_cache) == _LLM_CACHE_MAX

            # Verify that the first 10 entries were evicted (FIFO)
            for i in range(10):
                key = (LLMProvider.OPENAI, f"model-{i}", "", "")
                assert key not in _llm_cache

            for i in range(10, num_calls):
                key = (LLMProvider.OPENAI, f"model-{i}", "", "")
                assert key in _llm_cache
    finally:
        # Restore a clean state
        _llm_cache.clear()
