"""Public dispatch entry points — chat client construction, embeddings
construction, availability checks, and the process-lifetime chat-client
cache. Everything here dispatches through ``LLMProviderRegistry`` by
provider id instead of a hardcoded factory dict.
"""

from __future__ import annotations

import logging
import os
import threading

from langchain_core.embeddings import Embeddings

from core.llm_provider_management.enum_types import CLI_PROVIDERS, LLMProvider
from core.llm_provider_management.registry import get_llm_provider_registry

logger = logging.getLogger("whiskers")


def _provider_id(provider: "LLMProvider | str") -> str:
    """Normalize an ``LLMProvider`` enum member or raw string to a registry id."""
    if isinstance(provider, LLMProvider):
        return provider.value
    return str(provider)


def make_llm(
    provider: "LLMProvider | str",
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
):
    """Instantiate the chat LLM for *provider* via its registered ``ProviderSpec``.

    ``api_key`` / ``base_url`` override the environment when supplied (used by
    the dynamic LLM pool); otherwise env vars are the default fallback.
    """
    pid = _provider_id(provider)
    spec = get_llm_provider_registry().get(pid)
    if spec is None or spec.chat_factory is None:
        raise ValueError(f"Unknown or chat-incapable LLM provider: '{pid}'")
    return spec.chat_factory(model, api_key, base_url)


# Back-compat alias for the old private name.
_make_llm = make_llm


# Cache one chat-model instance per (provider, model, api_key, base_url) for
# the process lifetime. Reusing the instance avoids re-instantiating clients
# across entry points (agent CLI + run_graph) and keeps a stable object for
# implicit prompt caching.
_llm_cache: dict[tuple, object] = {}
# Guards in-memory LLM cache dict mutations synchronously (never held across await).
_llm_cache_lock = threading.Lock()
_LLM_CACHE_MAX = 32


def get_chat_llm(
    provider: "LLMProvider | str",
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
):
    """Return a cached chat model for *(provider, model, api_key, base_url)* (thread-safe).

    Double-checked locking — instantiated once per distinct config per process.
    The cache key includes ``api_key`` / ``base_url`` so a dynamic pool switch
    yields a fresh client instead of reusing a stale one.
    """
    key = (provider, model or "", api_key or "", base_url or "")
    cached = _llm_cache.get(key)
    if cached is not None:
        return cached
    with _llm_cache_lock:
        cached = _llm_cache.get(key)
        if cached is None:
            cached = make_llm(provider, model, api_key=api_key, base_url=base_url)
            if len(_llm_cache) >= _LLM_CACHE_MAX:
                _llm_cache.pop(next(iter(_llm_cache)))
            _llm_cache[key] = cached
        return cached


def default_embed_for(provider_str: str) -> tuple[str, int]:
    """Return ``(model, dimensions)`` defaults from the provider's ``ProviderSpec``.

    Source of truth is each module under ``providers/`` / ``cli_providers/``
    (``default_embed_model`` / ``default_embed_dimensions``). Hardcoded tables
    elsewhere should call this instead of duplicating model names.
    """
    p_str = (provider_str or "").strip().lower() or "openai"
    spec = get_llm_provider_registry().get(p_str)
    if spec is not None and (spec.default_embed_model or spec.default_embed_dimensions):
        model = spec.default_embed_model or "text-embedding-3-small"
        dims = int(spec.default_embed_dimensions or 1536)
        return model, dims
    return "text-embedding-3-small", 1536


def make_embeddings(
    provider_str: str,
    model: str,
    dimensions: int,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Embeddings:
    """Instantiate an embeddings client for *provider_str*.

    Dispatches to the provider module's ``embeddings_factory`` under
    ``providers/`` (or ``cli_providers/``). No silent cross-provider fallback.
    Providers without a factory (e.g. ``agy-cli`` / ``grok-cli``) raise — set
    ``EMBED_PROVIDER`` to ``openai``, ``gemini``, ``gemini-vertex``, or
    ``voyage``. ``anthropic`` / ``claude-cli`` use Voyage AI (never OpenAI).
    """
    p_str = (provider_str or "").strip().lower()
    reg = get_llm_provider_registry()
    spec = reg.get(p_str)
    if spec is None:
        raise ValueError(f"Unsupported embedding provider: '{provider_str}'")

    factory = spec.embeddings_factory
    if factory is None:
        raise ValueError(
            f"Provider '{provider_str}' has no embeddings API. "
            "Set EMBED_PROVIDER to openai|gemini|gemini-vertex|voyage "
            "(anthropic/claude-cli already use Voyage AI — never OpenAI)."
        )

    return factory(model, dimensions, api_key, base_url)


def _llm_available() -> bool:
    """Return True if the active ``LLM_PROVIDER`` provider is usable right now."""
    provider_str = os.environ.get("LLM_PROVIDER", "openai").strip().lower()
    spec = get_llm_provider_registry().get(provider_str)
    if spec is None or spec.is_available is None:
        return False
    return spec.is_available()


# Back-compat alias for the old public name.
llm_available = _llm_available

_LLM_AVAILABLE = _llm_available()

# Back-compat: enum -> env var name, for callers that inspected this table
# directly instead of going through the registry.
_PROVIDER_ENV_KEY: dict["LLMProvider", str] = {
    p: spec.env_key
    for p in LLMProvider
    if (spec := get_llm_provider_registry().get(p.value)) is not None and spec.env_key
}

# Back-compat alias — see enum_types.CLI_PROVIDERS.
_CLI_PROVIDERS = CLI_PROVIDERS
