"""cost_calculator: approximate USD estimate, never a billing source of truth."""

from core.telemetry.cost_calculator import estimate_cost


def test_exact_match():
    cost = estimate_cost("claude-sonnet-5", 1000, 1000)
    assert cost == 0.003 + 0.015


def test_case_insensitive_exact_match():
    assert estimate_cost("Claude-Sonnet-5", 1000, 0) == estimate_cost("claude-sonnet-5", 1000, 0)


def test_fallback_substring_match():
    cost = estimate_cost("claude-sonnet-5-20260301", 1000, 0)
    assert cost == 0.003


def test_unknown_model_returns_zero():
    assert estimate_cost("some-made-up-model-9000", 1000, 1000) == 0.0


def test_empty_model_returns_zero():
    assert estimate_cost("", 1000, 1000) == 0.0


def test_zero_tokens_returns_zero():
    assert estimate_cost("claude-sonnet-5", 0, 0) == 0.0
