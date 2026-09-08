"""
MTU-1 acceptance tests — multi-select clarify.

Ground truth for:
- build_clarify_questions candidate fallback is multiSelect=True, top-5, values=op_ids.
- format_clarification_hint folds a list answer into an ordered-chain hint and
  passes a single string through unchanged.
"""

from core_graph.clarify import build_clarify_questions, format_clarification_hint


def _cands(n):
    return [
        {
            "operation_id": f"op_{i}",
            "method": "POST",
            "path": f"/api/v1/thing/{i}",
            "description": f"desc {i}",
            "score": 0.5,
        }
        for i in range(n)
    ]


def test_candidate_fallback_is_multiselect_top5():
    qs = build_clarify_questions(candidates=_cands(8))
    assert len(qs) == 1
    q = qs[0]
    assert q["multiSelect"] is True
    assert q["header"] == "Action"
    # widened to top-5 (was top-3)
    assert len(q["options"]) == 5
    # option values carry the operation_id so the planner can chain them
    assert [o["value"] for o in q["options"]] == [f"op_{i}" for i in range(5)]


def test_candidate_fallback_fewer_than_five():
    qs = build_clarify_questions(candidates=_cands(2))
    assert qs[0]["multiSelect"] is True
    assert len(qs[0]["options"]) == 2


def test_llm_questions_multiselect_passthrough():
    # An LLM-supplied multiSelect question is preserved (not forced to single).
    llm_qs = [{
        "header": "Pick",
        "question": "Choose steps",
        "multiSelect": True,
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
    }]
    qs = build_clarify_questions(llm_questions=llm_qs)
    assert qs[0]["multiSelect"] is True
    assert len(qs[0]["options"]) == 2


def test_format_hint_list_is_ordered_chain():
    hint = format_clarification_hint(["post_add_export_request", "get_get_export_requests", "post_download_export"])
    assert "in order" in hint.lower()
    # preserves the given order
    assert hint.index("post_add_export_request") < hint.index("get_get_export_requests") < hint.index("post_download_export")


def test_format_hint_single_string_passthrough():
    assert format_clarification_hint("just one") == "just one"


def test_format_hint_empty_list():
    assert format_clarification_hint([]) == ""
