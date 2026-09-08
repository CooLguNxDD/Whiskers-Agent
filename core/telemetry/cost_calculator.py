"""Approximate USD cost estimation for LLM token usage.

Debugging aid only (feeds Phoenix span attributes / future admin display) —
never a billing source of truth. `LLMPoolEntry` (db_layer/models/config.py)
has no price column today, so rates are a static, operator-editable catalog
here rather than DB-sourced; keep it aligned with CLAUDE.md's LLM providers
table when models change.
"""

import logging

logger = logging.getLogger("whiskers.telemetry")

# Rates per 1000 tokens (USD), (prompt, completion). Approximate — verify
# against the provider's current pricing page before trusting an exact figure.
_RATES: dict[str, tuple[float, float]] = {
    # Anthropic (this project's default chat provider — see CLAUDE.md §"LLM providers")
    "claude-opus-5": (0.015, 0.075),
    "claude-sonnet-5": (0.003, 0.015),
    "claude-fable-5": (0.0008, 0.004),
    "claude-haiku-4-5": (0.001, 0.005),
    # Legacy Anthropic ids still seen in older audit logs / pinned configs
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-haiku": (0.00025, 0.00125),
    "claude-3-opus": (0.015, 0.075),
    # OpenAI
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    # Google (gemini-3.1-flash-lite is this project's default gemini chat model)
    "gemini-3.1-flash-lite": (0.0001, 0.0004),
    "gemini-3.5-flash-lite": (0.0001, 0.0004),
    "gemini-1.5-pro": (0.0035, 0.0105),
    # Voyage (this project's default embedding provider — voyage-4)
    "voyage-4": (0.00006, 0.0),
    # DeepSeek
    "deepseek-chat": (0.00014, 0.00028),
    "deepseek-coder": (0.00014, 0.00028),
}

# Ordered substring fallbacks for partial/versioned model-id matches, checked
# after an exact lowercase match fails.
_FALLBACK_ORDER = (
    "claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-haiku-4-5",
    "opus", "sonnet", "haiku",
    "gpt-4o-mini", "gpt-4o",
    "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-1.5-pro", "gemini",
    "voyage",
    "deepseek",
)


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate cost in USD for one call. Returns 0.0 for an unrecognized model."""
    if not model:
        return 0.0
    model_lower = model.lower()

    rates = _RATES.get(model_lower)
    if rates is None:
        for key in _FALLBACK_ORDER:
            if key in model_lower:
                rates = _RATES.get(key)
                if rates is not None:
                    break

    if rates is None:
        logger.debug("cost_calculator: no pricing entry for model '%s'", model)
        return 0.0

    prompt_rate, completion_rate = rates
    return (prompt_tokens / 1000.0) * prompt_rate + (completion_tokens / 1000.0) * completion_rate
