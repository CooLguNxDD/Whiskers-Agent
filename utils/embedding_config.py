"""Config loader for embedding_config.json.

Loads once at import time. Exposes active embedding model prefix templates
and formatting helpers.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from utils.config_registry import get_config_registry

logger = logging.getLogger("whiskers")

EMBEDDING_CONFIG: dict[str, Any] = get_config_registry().embedding

_DEFAULT_PROFILE: dict[str, Any] = {
    "provider": "openai",
    "model": "text-embedding-3-small",
    "dimensions": 1536,
    "query_prefix": "{text}",
    "document_prefix": "{text}"
}


def get_active_profile() -> dict[str, Any]:
    """Resolve the active embedding model profile from config or environment.

    active may be overridden by env EMBED_PROFILE.
    Falls back to OpenAI symmetric ({text}) templates if the file or profile
    is missing.
    """
    active = os.environ.get("EMBED_PROFILE")
    if not active:
        active = EMBEDDING_CONFIG.get("active")

    if not active:
        return _DEFAULT_PROFILE

    models = EMBEDDING_CONFIG.get("models", {})
    profile = models.get(active)
    if not profile:
        return _DEFAULT_PROFILE

    return profile


def selection_for_profile(name: str) -> dict[str, Any]:
    """Resolve an explicit profile for shared embedding APIs (credentials stay in runtime env)."""
    profile = EMBEDDING_CONFIG.get("models", {}).get(name)
    if not isinstance(profile, dict):
        raise ValueError("Unknown embedding profile")
    fields = ("provider", "model", "dimensions", "base_url", "batch_size", "max_concurrency",
              "max_media_bytes", "timeout_seconds")
    return {k: profile[k] for k in fields if k in profile}


def environment_embedding_selection() -> dict[str, Any]:
    """Resolve opt-in multimodal profiles; retain legacy text environment model defaults."""
    from core.llm_provider_management import default_embed_for, get_llm_provider_registry
    profile = get_active_profile()
    spec = get_llm_provider_registry().get(profile.get("provider", ""))
    # Legacy text profiles historically control prefixes only; do not switch their model.
    selection = dict(profile) if spec and spec.multimodal_embeddings_factory else {}
    provider = os.environ.get("EMBED_PROVIDER") or selection.get("provider") or os.environ.get("LLM_PROVIDER", "openai")
    provider = provider.lower()
    if provider != selection.get("provider"):
        selection = {}
    model = os.environ.get("EMBED_MODEL") or selection.get("model")
    dims = os.environ.get("EMBED_DIMENSIONS") or selection.get("dimensions")
    if not model or not dims:
        default_model, default_dims = default_embed_for(provider)
        model, dims = model or default_model, dims or default_dims
    selected_spec = get_llm_provider_registry().get(provider)
    if selected_spec and selected_spec.multimodal_embeddings_factory:
        if type(dims) not in (int, str) or int(dims) < 1:
            raise ValueError("multimodal dimensions must be a configured positive integer")
    selection.update({
        "provider": provider, "model": model, "dimensions": int(dims),
        "api_key": os.environ.get("EMBED_API_KEY") or os.environ.get("VOYAGE_API_KEY"),
        "base_url": os.environ.get("EMBED_BASE_URL") or selection.get("base_url") or os.environ.get("VOYAGE_BASE_URL"),
        "source": "env",
    })
    return selection


def format_query(text: str) -> str:
    """Format the query text with the active profile's query prefix.

    Returns the raw text as a no-op if the query prefix is the bare '{text}' placeholder.
    """
    profile = get_active_profile()
    prefix = profile.get("query_prefix", "{text}")
    if prefix == "{text}":
        return text
    return prefix.format(text=text)


def format_document(text: str, title: str | None = None) -> str:
    """Format the document text with the active profile's document prefix.

    Returns the raw text as a no-op if the document prefix is the bare '{text}' placeholder.
    Tolerant of templates that do not reference '{title}'.
    """
    profile = get_active_profile()
    prefix = profile.get("document_prefix", "{text}")
    if prefix == "{text}":
        return text

    default_title = profile.get("default_title", "none")
    t_val = title or default_title
    return prefix.format(text=text, title=t_val)
