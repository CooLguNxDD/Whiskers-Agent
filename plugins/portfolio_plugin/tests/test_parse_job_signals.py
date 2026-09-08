"""Unit tests for agents.bake._parse_job_signals's free-text "ROLE at COMPANY" parse."""

from plugins.portfolio_plugin.agents.bake import _parse_job_signals


def test_parse_job_signals_role_containing_the_word_for_substring():
    """Regression: a role name that merely contains the substring "for"
    (e.g. "Platform", "Performance") must not be mangled by the "for"
    splitter. Live-observed: "AI Platform Engineer at Anthropic" parsed
    role as "m Engineer" (Plat|for|m Engineer split on bare substring).
    """
    sig = _parse_job_signals("AI Platform Engineer at Anthropic.", None)
    assert sig["role"] == "AI Platform Engineer"
    assert sig["company"] == "Anthropic"


def test_parse_job_signals_strips_bake_prefix():
    sig = _parse_job_signals("bake portfolio for Senior Engineer at Acme", None)
    assert sig["role"] == "Senior Engineer"
    assert sig["company"] == "Acme"


def test_parse_job_signals_performance_engineer_role():
    sig = _parse_job_signals("Performance Engineer at Globex.", None)
    assert sig["role"] == "Performance Engineer"
    assert sig["company"] == "Globex"


def test_parse_job_signals_explicit_signals_win_over_parse():
    sig = _parse_job_signals(
        "AI Platform Engineer at Anthropic.",
        {"company": "Explicit Co", "role": "Explicit Role"},
    )
    assert sig["role"] == "Explicit Role"
    assert sig["company"] == "Explicit Co"
