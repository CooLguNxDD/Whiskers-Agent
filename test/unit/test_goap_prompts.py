"""Unit tests for GOAP goal extraction and goal evaluation prompts and helper formatting functions."""

import pytest
from core_graph.prompts.context_block import format_context_block
from core_graph.prompts.goap_goal_prompt import GOAP_GOAL_PROMPT
from core_graph.prompts.goal_check_prompt import GOAL_CHECK_PROMPT
from core_graph.prompts.triage_prompt import TRIAGE_PROMPT


def test_goap_prompts_imported_and_valid():
    """Assert goap prompts import, are non-empty, and mention JSON."""
    assert isinstance(GOAP_GOAL_PROMPT, str)
    assert len(GOAP_GOAL_PROMPT.strip()) > 0
    assert "JSON" in GOAP_GOAL_PROMPT

    assert isinstance(GOAL_CHECK_PROMPT, str)
    assert len(GOAL_CHECK_PROMPT.strip()) > 0
    assert "JSON" in GOAL_CHECK_PROMPT


def test_goap_prompts_key_terms():
    """Assert GOAP prompts mention their respective key JSON fields/instructions."""
    assert "goal" in GOAP_GOAL_PROMPT
    assert "seed_facts" in GOAP_GOAL_PROMPT

    assert "done" in GOAL_CHECK_PROMPT
    assert "next_hint" in GOAL_CHECK_PROMPT


def test_format_context_block_empty():
    """Empty history/memory still yields the always-on UTC timestamp line."""
    for args in ((None, None, None), ([], {}, "")):
        block = format_context_block(*args)
        assert "Current time (UTC):" in block
        assert "Conversation so far:" not in block
        assert "working memory" not in block
        assert "Prior result summary" not in block


def test_format_context_block_full():
    """Assert format_context_block correctly formats and respects max_turns."""
    history = [
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "turn 2"},
        {"role": "user", "content": "turn 3"},
        {"role": "assistant", "content": "turn 4"},
        {"role": "user", "content": "turn 5"},
        {"role": "assistant", "content": "turn 6"},
        {"role": "user", "content": "turn 7"},
        {"role": "assistant", "content": "turn 8"},
    ]
    working_memory = {"record_id": 123}
    last_summary = "Found 3 records"

    result = format_context_block(history, working_memory, last_summary, max_turns=6)

    assert "Conversation so far:" in result
    assert "record_id=123" in result
    assert "Prior result summary: Found 3 records" in result

    # Check that at most 6 turn lines are included
    lines = result.splitlines()
    role_lines = [line for line in lines if line.strip().startswith("- ")]
    assert len(role_lines) <= 6

    # Verify oldest-first order (turns 3 to 8 are the last 6 turns)
    assert "- user: turn 3" in result
    assert "- assistant: turn 8" in result
    assert "turn 1" not in result
    assert "turn 2" not in result


def test_format_context_block_truncates_long_entries():
    """Per-entry content is capped so one large AIMessage cannot dominate the block."""
    history = [{"role": "assistant", "content": "x" * 1200}]
    result = format_context_block(history, max_chars=800)
    line = [ln for ln in result.splitlines() if ln.startswith("- assistant:")][0]
    body = line[len("- assistant:"):].lstrip()
    assert body.endswith("…")
    assert len(body) == 801  # 800 chars + ellipsis
    assert "x" * 801 not in result


def test_triage_prompt_contract():
    """Assert TRIAGE_PROMPT still contains the original JSON contracts."""
    assert isinstance(TRIAGE_PROMPT, str)
    assert '"mode": "chat"' in TRIAGE_PROMPT
    assert '"mode": "classic"' in TRIAGE_PROMPT
    assert '"mode": "task"' in TRIAGE_PROMPT  # legacy alias still documented
    # Verify our appended instruction is present
    assert "Conversation Context" in TRIAGE_PROMPT
    assert "tell me more about it" in TRIAGE_PROMPT
    assert "portfolio_ask_v1" in TRIAGE_PROMPT
