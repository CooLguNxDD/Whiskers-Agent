"""Gemini Vertex AI (Express mode) provider spec.

Plain API key, no GCP project/IAM — via the unified google-genai client
(``vertexai=True``). ``ChatVertexAI``/``VertexAIEmbeddings`` from
``langchain-google-vertexai`` are deprecated and have no api-key support, so
this routes through the lib-recommended ``langchain-google-genai`` classes,
same as plain ``gemini``, plus env isolation to avoid leaking a plain-Gemini
key into the Vertex client.
"""

from __future__ import annotations

import logging
import os

from core.llm_provider_management.base import isolated_vertex_env
from core.llm_provider_management.providers.gemini import get_gemini_embeddings_cls
from core.llm_provider_management.registry import register_provider
from core.llm_provider_management.spec import ProviderSpec

logger = logging.getLogger("whiskers")


def _chat(model: str, api_key: str | None, base_url: str | None):  # noqa: ARG001 - base_url unused
    """Build a ChatGoogleGenerativeAI client in Vertex Express mode."""
    from langchain_google_genai import ChatGoogleGenerativeAI
    k = api_key or os.environ.get("GOOGLE_VERTEX_API_KEY")
    kwargs = {"model": model or "gemini-3.1-flash-lite", "temperature": 0, "vertexai": True}
    if k:
        kwargs["google_api_key"] = k
    # Use isolated env (no GOOGLE_API_KEY/GEMINI_API_KEY) during construction
    # to prevent plain-gemini keys from being picked up by the underlying client.
    with isolated_vertex_env():
        return ChatGoogleGenerativeAI(**kwargs)


def _embeddings(model: str, dimensions: int, api_key: str | None, base_url: str | None):  # noqa: ARG001
    """Build the shared GeminiEmbeddings wrapper client in Vertex Express mode."""
    logger.info(f"Embeddings: GoogleGenerativeAI Vertex express ({model}, dim={dimensions})")
    GeminiEmbeddings = get_gemini_embeddings_cls()
    k = api_key or os.environ.get("GOOGLE_VERTEX_API_KEY")
    kwargs = {"model": model, "output_dimensionality": dimensions, "vertexai": True}
    if k:
        kwargs["google_api_key"] = k
    with isolated_vertex_env():
        return GeminiEmbeddings(**kwargs)


def _is_available() -> bool:
    """Vertex Express is available with a Vertex API key or the generic LLM_API_KEY override."""
    return bool(os.environ.get("GOOGLE_VERTEX_API_KEY", "").strip()) or bool(os.environ.get("LLM_API_KEY", "").strip())


SPEC = ProviderSpec(
    id="gemini-vertex",
    is_cli=False,
    env_key="GOOGLE_VERTEX_API_KEY",
    default_chat_model="gemini-3.1-flash-lite",
    default_embed_model="gemini-embedding-001",
    default_embed_dimensions=1536,
    chat_factory=_chat,
    embeddings_factory=_embeddings,
    is_available=_is_available,
)

register_provider(SPEC)
