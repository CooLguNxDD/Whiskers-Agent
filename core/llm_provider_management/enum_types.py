"""``LLMProvider`` enum — kept for back-compat coercion (``LLMProvider(provider_str)``)
and pool validation call sites that still want a closed, typed set of "known"
providers.

Dispatch itself goes through the registry by string id (``ProviderSpec.id``),
not by enum identity — a new provider can register a ``ProviderSpec`` without
ever getting an enum member. See ``registry.get_llm_provider_registry``.
"""

from __future__ import annotations

from enum import Enum


class LLMProvider(Enum):
    """Provider class for LLM integrations."""
    OPENAI        = "openai"
    ANTHROPIC     = "anthropic"
    GEMINI        = "gemini"
    GEMINI_VERTEX = "gemini-vertex"
    CLAUDE_CLI    = "claude-cli"   # headless Claude Code CLI subprocess
    AGY_CLI       = "agy-cli"      # headless Antigravity (agy) CLI subprocess
    GROK_CLI      = "grok-cli"     # headless xAI Grok CLI subprocess


# Built-in CLI provider members — used where callers need to branch on
# "is this the CLI-subprocess kind" without hardcoding provider ids.
CLI_PROVIDERS = frozenset({LLMProvider.CLAUDE_CLI, LLMProvider.AGY_CLI, LLMProvider.GROK_CLI})
