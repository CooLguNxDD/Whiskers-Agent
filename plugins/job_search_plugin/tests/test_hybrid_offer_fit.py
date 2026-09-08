"""Unit tests for the Stage 4 hybrid (RRF dense+sparse) retrieval adapter.

store.search_preferences_hybrid is a thin adapter over
db_layer/embeddings/search_engine.py — the single hybrid-search choke point.
These tests mock search_engine.search itself (matching
test/unit/test_search_engine.py's own pattern for adapter-level tests) rather
than reinventing hybrid-search test scaffolding.
"""

import pytest
from unittest import mock


@pytest.mark.asyncio
async def test_search_preferences_hybrid_calls_engine_with_tenant_filter():
    from plugins.job_search_plugin import store

    fake_sel = {"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536}
    fake_results = [
        {"content": "5 years Python", "source": "resume", "applicant_profile_id": 7, "similarity": 0.87},
    ]

    with mock.patch(
        "core.llm_config_service.resolve_tool_embedding",
        new_callable=mock.AsyncMock,
    ) as mock_resolve, mock.patch(
        "plugins.job_search_plugin.store.search_engine_search", new_callable=mock.AsyncMock
    ) as mock_search:
        mock_resolve.return_value = fake_sel
        mock_search.return_value = fake_results

        res = await store.search_preferences_hybrid(7, "Python remote job", top_k=8)

    assert res == fake_results
    mock_resolve.assert_awaited_once_with("job_search_plugin", "index_preferences")
    mock_search.assert_awaited_once()
    call_args, call_kwargs = mock_search.await_args
    assert call_args[0] is store._JOB_PREF_SEARCH_SPEC
    assert call_args[1] == "Python remote job"
    assert call_args[2] == fake_sel
    assert call_args[3] == 8
    assert "extra_filters" in call_kwargs


@pytest.mark.asyncio
async def test_job_pref_search_spec_shape():
    from plugins.job_search_plugin import store
    from plugins.job_search_plugin.models import JobPreferenceEmbedding

    spec = store._JOB_PREF_SEARCH_SPEC
    assert spec.name == "job_preferences"
    assert spec.model is JobPreferenceEmbedding
    # search_doc_col non-None -> hybrid-capable per search_engine.search()'s contract.
    assert spec.search_doc_col is not None
    row = mock.Mock(content="c", source="resume", applicant_profile_id=7)
    assert spec.row_to_dict(row) == {"content": "c", "source": "resume", "applicant_profile_id": 7}
    assert spec.key_fn({"applicant_profile_id": 7, "source": "resume", "content": "c"}) == (7, "resume", "c")
