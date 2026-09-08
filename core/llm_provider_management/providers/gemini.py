"""Gemini (Google AI Studio) provider spec — chat + embeddings via langchain-google-genai.

Shared Gemini embeddings wrapper also used by ``gemini_vertex``.
"""

from __future__ import annotations

import logging
import os

from core.llm_provider_management.registry import register_provider
from core.llm_provider_management.spec import ProviderSpec

logger = logging.getLogger("whiskers")

_gemini_embeddings_cls: type | None = None


def get_gemini_embeddings_cls():
    """Lazily build and cache the shared GeminiEmbeddings wrapper subclass.

    Used by both plain ``gemini`` and ``gemini-vertex`` to avoid duplication.
    """
    global _gemini_embeddings_cls
    if _gemini_embeddings_cls is None:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        class _GeminiEmbeddings(GoogleGenerativeAIEmbeddings):
            """Bypass list-aggregation bugs by embedding documents one-by-one."""

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                """Embed a list of documents synchronously."""
                return [self.embed_query(text) for text in texts]

            async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
                """Embed documents asynchronously with limited concurrency."""
                import asyncio
                semaphore = asyncio.Semaphore(10)

                async def embed_with_semaphore(text: str) -> list[float]:
                    async with semaphore:
                        return await self.aembed_query(text)

                return list(await asyncio.gather(*[embed_with_semaphore(text) for text in texts]))

        _gemini_embeddings_cls = _GeminiEmbeddings
    return _gemini_embeddings_cls


def _chat(model: str, api_key: str | None, base_url: str | None):  # noqa: ARG001 - base_url unused
    """Build a ChatGoogleGenerativeAI client.

    2.5 models have implicit context caching enabled by default — repeated
    prompt prefixes across recent calls are auto-discounted (no setup).
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    kwargs = {"model": model or "gemini-3.1-flash-lite", "temperature": 0}
    if api_key:
        kwargs["google_api_key"] = api_key
    return ChatGoogleGenerativeAI(**kwargs)


def _embeddings(model: str, dimensions: int, api_key: str | None, base_url: str | None):  # noqa: ARG001
    """Build the shared GeminiEmbeddings wrapper client."""
    logger.info(f"Embeddings: GoogleGenerativeAI ({model}, dim={dimensions})")
    GeminiEmbeddings = get_gemini_embeddings_cls()
    kwargs = {"model": model, "output_dimensionality": dimensions}
    if api_key:
        kwargs["google_api_key"] = api_key
    return GeminiEmbeddings(**kwargs)


def _is_available() -> bool:
    """Gemini is available with a Google API key or the generic LLM_API_KEY override."""
    return bool(os.environ.get("GOOGLE_API_KEY", "").strip()) or bool(os.environ.get("LLM_API_KEY", "").strip())


SPEC = ProviderSpec(
    id="gemini",
    is_cli=False,
    env_key="GOOGLE_API_KEY",
    default_chat_model="gemini-3.1-flash-lite",
    default_embed_model="gemini-embedding-001",
    default_embed_dimensions=1536,
    chat_factory=_chat,
    embeddings_factory=_embeddings,
    is_available=_is_available,
)

register_provider(SPEC)
