"""
Unit tests for evaluating applicant fit and preference indexing (acceptance tests).
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import hashlib

from plugins.job_search_plugin.MCPTools.evaluate_tools import (
    parse_fit_verdict,
    index_preferences,
    evaluate_offer_fit,
)
from plugins.job_search_plugin.store import add_preference_embedding


def test_parse_fit_verdict_fenced_json():
    """Verify parse_fit_verdict parses a fenced markdown JSON block."""
    raw_response = """
    Some pre-text before the JSON.
    ```json
    {
        "fit_score": 0.85,
        "skill_match_reasons": ["Python", "FastAPI"],
        "preference_match_reasons": ["Remote work"],
        "mismatch_reasons": [],
        "recommended": true
    }
    ```
    Post-text.
    """
    res = parse_fit_verdict(raw_response)
    assert res["fit_score"] == 0.85
    assert res["skill_match_reasons"] == ["Python", "FastAPI"]
    assert res["preference_match_reasons"] == ["Remote work"]
    assert res["mismatch_reasons"] == []
    assert res["recommended"] is True


def test_parse_fit_verdict_clamps_and_coerces():
    """Verify parse_fit_verdict clamps fit_score and coerces recommended to bool."""
    # Test high clamp
    raw_high = '{"fit_score": 1.5, "recommended": "yes"}'
    res_high = parse_fit_verdict(raw_high)
    assert res_high["fit_score"] == 1.0
    assert res_high["recommended"] is True

    # Test low clamp
    raw_low = '{"fit_score": -0.5, "recommended": ""}'
    res_low = parse_fit_verdict(raw_low)
    assert res_low["fit_score"] == 0.0
    assert res_low["recommended"] is False


def test_parse_fit_verdict_garbage():
    """Verify parse_fit_verdict on garbage returns safe default with recommended=False."""
    garbage = "This is not json at all."
    res = parse_fit_verdict(garbage)
    assert res == {
        "fit_score": 0.0,
        "skill_match_reasons": [],
        "preference_match_reasons": [],
        "mismatch_reasons": ["unparseable verdict"],
        "recommended": False,
    }


def test_parse_fit_verdict_langchain_content_block_list():
    """Live-observed with Gemini: resp.content can be a multi-block list
    ([{"type": "text", "text": "..."}]) instead of a plain string.
    """
    raw_blocks = [{"type": "text", "text": '{"fit_score": 0.4, "recommended": false, "mismatch_reasons": ["gap"]}'}]
    res = parse_fit_verdict(raw_blocks)
    assert res["fit_score"] == 0.4
    assert res["recommended"] is False
    assert res["mismatch_reasons"] == ["gap"]


def test_parse_fit_verdict_empty_content_block_list():
    res = parse_fit_verdict([])
    assert res["mismatch_reasons"] == ["unparseable verdict"]


def _mock_llm_with_verdict():
    mock_llm_response = MagicMock()
    mock_llm_response.content = """
    {
        "fit_score": 0.9,
        "skill_match_reasons": ["Has Python experience"],
        "preference_match_reasons": ["Remote match"],
        "mismatch_reasons": [],
        "recommended": true
    }
    """
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_llm_response)
    return mock_llm


@pytest.mark.asyncio
async def test_evaluate_offer_fit_hybrid_default():
    """Default hybrid=True retrieves via search_preferences_hybrid, not the dense-only path."""
    mock_chunks = [
        {"source": "resume", "content": "5 years Python experience", "similarity": 0.9},
        {"source": "preferences", "content": "Wants Remote work", "similarity": 0.8},
    ]
    mock_llm = _mock_llm_with_verdict()

    with patch("plugins.job_search_plugin.MCPTools.evaluate_tools.search_preferences_hybrid", AsyncMock(return_value=mock_chunks)) as mock_hybrid, \
         patch("plugins.job_search_plugin.MCPTools.evaluate_tools.search_preferences", AsyncMock()) as mock_dense, \
         patch("plugins.job_search_plugin.MCPTools.evaluate_tools.get_chat_llm", return_value=mock_llm) as mock_get_llm:

        res = await evaluate_offer_fit(applicant_profile_id=42, offer_text="Python Remote Job")

        assert res["status"] == "ok"
        assert res["hybrid_used"] is True
        assert res["verdict"]["fit_score"] == 0.9
        assert len(res["retrieved"]) == 2
        assert res["retrieved"][0]["source"] == "resume"

        mock_hybrid.assert_called_once_with(42, "Python Remote Job", top_k=8)
        mock_dense.assert_not_called()
        mock_get_llm.assert_called_once()
        mock_llm.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_evaluate_offer_fit_hybrid_false_matches_pre_stage4_behavior():
    """hybrid=False takes the dense-only cosine path (search_preferences), unchanged."""
    mock_qvec = [0.1, 0.2, 0.3]
    mock_chunks = [
        {"source": "resume", "content": "5 years Python experience", "similarity": 0.9},
    ]
    mock_llm = _mock_llm_with_verdict()

    with patch("plugins.job_search_plugin.MCPTools.evaluate_tools.embed", AsyncMock(return_value=mock_qvec)) as mock_embed, \
         patch("plugins.job_search_plugin.MCPTools.evaluate_tools.search_preferences", AsyncMock(return_value=mock_chunks)) as mock_search, \
         patch("plugins.job_search_plugin.MCPTools.evaluate_tools.search_preferences_hybrid", AsyncMock()) as mock_hybrid, \
         patch("plugins.job_search_plugin.MCPTools.evaluate_tools.get_chat_llm", return_value=mock_llm):

        res = await evaluate_offer_fit(
            applicant_profile_id=42, offer_text="Python Remote Job", hybrid=False
        )

        assert res["status"] == "ok"
        assert res["hybrid_used"] is False
        assert len(res["retrieved"]) == 1

        mock_embed.assert_called_once_with("Python Remote Job")
        mock_search.assert_called_once_with(42, mock_qvec, top_k=8)
        mock_hybrid.assert_not_called()


@pytest.mark.asyncio
async def test_index_preferences():
    """Verify index_preferences chunks resume+preferences, embeds them (via the
    resolved model selection, not the bare global-default embed()), and writes
    to store tagged with model_id_for(sel) — required for Stage 4 hybrid
    retrieval's model_col filter to ever match these rows.
    """
    mock_profile = {
        "id": 42,
        "base_resume_text": "Resume line 1.\n\nResume line 2.",
        "preferences_text": "Pref line 1.\n\nPref line 2.",
    }
    mock_vec = [0.5, 0.6, 0.7]
    fake_sel = {"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536}

    with patch("plugins.job_search_plugin.MCPTools.evaluate_tools.get_profile", AsyncMock(return_value=mock_profile)) as mock_get_profile, \
         patch("core.llm_config_service.resolve_tool_embedding", AsyncMock(return_value=fake_sel)) as mock_resolve, \
         patch("db_layer.embeddings.embeddings_core.embed_query_with", AsyncMock(return_value=mock_vec)) as mock_embed, \
         patch("plugins.job_search_plugin.MCPTools.evaluate_tools.add_preference_embedding", AsyncMock()) as mock_add_embedding:

        res = await index_preferences(applicant_profile_id=42, preferences_text="Custom Pref 1.\n\nCustom Pref 2.")

        assert res == {
            "status": "ok",
            "indexed": 4,
            "sources": {
                "resume": 2,
                "preferences": 2,
            },
        }

        # Assert get_profile called
        mock_get_profile.assert_called_once_with(42)
        mock_resolve.assert_awaited_once_with("job_search_plugin", "index_preferences")

        # Assert embed called for each chunk (2 resume chunks + 2 preference chunks = 4)
        assert mock_embed.call_count == 4
        # Assert add_preference_embedding called for each chunk, tagged with the model id
        assert mock_add_embedding.call_count == 4
        for call in mock_add_embedding.await_args_list:
            assert call.kwargs["model"] == "openai:text-embedding-3-small:1536"


@pytest.mark.asyncio
async def test_index_preferences_missing_profile():
    """Verify index_preferences returns error if profile is not found."""
    with patch("plugins.job_search_plugin.MCPTools.evaluate_tools.get_profile", AsyncMock(return_value=None)):
        res = await index_preferences(applicant_profile_id=999)
        assert res == {"status": "error", "error": "profile_not_found"}


@pytest.mark.asyncio
async def test_add_preference_embedding_store():
    """Verify add_preference_embedding constructs expected statement and executes/commits."""
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("plugins.job_search_plugin.store.get_async_session", return_value=mock_session_ctx):
        res = await add_preference_embedding(
            applicant_profile_id=123,
            source="resume",
            content="Some chunk content",
            embedding=[0.1, 0.2],
            model="text-embedding-3-small",
        )

        expected_hash = hashlib.sha256("Some chunk content".encode("utf-8")).hexdigest()
        assert res == {
            "applicant_profile_id": 123,
            "source": "resume",
            "content_hash": expected_hash,
        }

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()
