"""Headless Claude Code CLI subprocess provider spec.

Chat runs via the ``claude`` CLI. Embeddings reuse Voyage AI from
``providers.voyage`` (Anthropic's recommended path) — never OpenAI.
"""

from __future__ import annotations

from core.llm_provider_management.cli_providers._shared import make_cli_is_available
from core.llm_provider_management.providers.voyage import (
    DEFAULT_DIMENSIONS as VOYAGE_DEFAULT_DIMENSIONS,
    DEFAULT_MODEL as VOYAGE_DEFAULT_MODEL,
)
from core.llm_provider_management.registry import register_provider
from core.llm_provider_management.spec import ProviderSpec


def _chat(model: str, api_key: str | None, base_url: str | None):
    """Build a CliAgentChatModel driving the ``claude`` CLI binary."""
    from core_graph.goap_agent.llm.cli_chat_model import make_cli_chat_model
    return make_cli_chat_model("claude-cli", model or "", api_key=api_key, base_url=base_url)


def _embeddings(model: str, dimensions: int, api_key: str | None, base_url: str | None):
    """Build Voyage AI embeddings (Anthropic-recommended; not OpenAI)."""
    from core.llm_provider_management.providers.voyage import make_voyage_embeddings
    return make_voyage_embeddings(model, dimensions, api_key, base_url)


SPEC = ProviderSpec(
    id="claude-cli",
    is_cli=True,
    env_key=None,
    default_chat_model="",
    default_embed_model=VOYAGE_DEFAULT_MODEL,
    default_embed_dimensions=VOYAGE_DEFAULT_DIMENSIONS,
    chat_factory=_chat,
    embeddings_factory=_embeddings,
    is_available=make_cli_is_available("CLAUDE_CLI_BINARY", "claude"),
)

register_provider(SPEC)
