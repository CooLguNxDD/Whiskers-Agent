"""Anthropic provider spec — chat (ChatAnthropic) + Voyage AI embeddings.

Anthropic has no native embeddings API. Per their docs we reuse the Voyage
factory from ``providers.voyage`` — never a silent OpenAI embeddings fallback.
"""

from __future__ import annotations

import logging
import os

from core.llm_provider_management.providers.voyage import (
    DEFAULT_DIMENSIONS as VOYAGE_DEFAULT_DIMENSIONS,
    DEFAULT_MODEL as VOYAGE_DEFAULT_MODEL,
)
from core.llm_provider_management.registry import register_provider
from core.llm_provider_management.spec import ProviderSpec

logger = logging.getLogger("whiskers")


def _chat(model: str, api_key: str | None, base_url: str | None):  # noqa: ARG001 - base_url unused (no local-endpoint support)
    """Build a ChatAnthropic client."""
    from langchain_anthropic import ChatAnthropic
    kwargs = {"model": model or "claude-sonnet-5", "temperature": 0}
    if api_key:
        kwargs["api_key"] = api_key
    return ChatAnthropic(**kwargs)


def _embeddings(model: str, dimensions: int, api_key: str | None, base_url: str | None):
    """Build Voyage AI embeddings (Anthropic-recommended; not OpenAI)."""
    from core.llm_provider_management.providers.voyage import make_voyage_embeddings
    return make_voyage_embeddings(model, dimensions, api_key, base_url)


def _is_available() -> bool:
    """Chat availability follows ANTHROPIC_API_KEY (or LLM_API_KEY override).

    Embeddings are separate (VOYAGE_API_KEY) and checked when the embeddings
    client is constructed.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()) or bool(os.environ.get("LLM_API_KEY", "").strip())


SPEC = ProviderSpec(
    id="anthropic",
    is_cli=False,
    env_key="ANTHROPIC_API_KEY",
    default_chat_model="claude-sonnet-5",
    default_embed_model=VOYAGE_DEFAULT_MODEL,
    default_embed_dimensions=VOYAGE_DEFAULT_DIMENSIONS,
    chat_factory=_chat,
    embeddings_factory=_embeddings,
    is_available=_is_available,
)

register_provider(SPEC)
