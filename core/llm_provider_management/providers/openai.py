"""OpenAI provider spec — chat (ChatOpenAI) + embeddings (OpenAIEmbeddings).

No longer a silent fallback for other providers. Anthropic / claude-cli use
Voyage AI; other CLI providers require an explicit ``EMBED_PROVIDER``.
"""

from __future__ import annotations

import logging
import os

from core.llm_provider_management.registry import register_provider
from core.llm_provider_management.spec import ProviderSpec

logger = logging.getLogger("whiskers")


def _chat(model: str, api_key: str | None, base_url: str | None):
    """Build a ChatOpenAI client; supports LM Studio-style local endpoints via base_url."""
    from langchain_openai import ChatOpenAI
    b = base_url or os.environ.get("LLM_BASE_URL")
    k = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")

    kwargs = {"model": model or "gpt-4o", "temperature": 0}

    if b:
        kwargs["base_url"] = b
        if not k:
            k = "lm-studio"

    if k:
        kwargs["api_key"] = k

    return ChatOpenAI(**kwargs)


def _embeddings(model: str, dimensions: int, api_key: str | None, base_url: str | None):
    """Build an OpenAIEmbeddings client; auto-disables ctx-length check for local endpoints."""
    from langchain_openai import OpenAIEmbeddings
    logger.info(f"Embeddings: OpenAI ({model}, dim={dimensions})")
    kwargs = {"model": model, "dimensions": dimensions}
    if base_url:
        kwargs["openai_api_base"] = base_url
        if "lmstudio" in base_url.lower() or "127.0.0.1" in base_url or "localhost" in base_url or "host.docker.internal" in base_url:
            kwargs["check_embedding_ctx_length"] = False
    if api_key:
        kwargs["openai_api_key"] = api_key
    return OpenAIEmbeddings(**kwargs)


def _is_available() -> bool:
    """OpenAI is available with an API key, an LLM_API_KEY override, or a local base URL."""
    if os.environ.get("LLM_BASE_URL", "").strip():
        return True
    return bool(os.environ.get("OPENAI_API_KEY", "").strip()) or bool(os.environ.get("LLM_API_KEY", "").strip())


SPEC = ProviderSpec(
    id="openai",
    is_cli=False,
    env_key="OPENAI_API_KEY",
    default_chat_model="gpt-4o",
    default_embed_model="text-embedding-3-small",
    default_embed_dimensions=1536,
    chat_factory=_chat,
    embeddings_factory=_embeddings,
    is_available=_is_available,
)

register_provider(SPEC)
