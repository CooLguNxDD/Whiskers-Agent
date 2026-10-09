"""
Core embedding factory and utilities.

The embedding provider defaults to LLM_PROVIDER, but can be overridden with EMBED_PROVIDER:
  LLM_PROVIDER=openai      → OpenAIEmbeddings (default: text-embedding-3-small, dim=1536)
  LLM_PROVIDER=gemini      → GoogleGenerativeAIEmbeddings (default: gemini-embedding-001, dim=1536)
  LLM_PROVIDER=anthropic   → Voyage AI (default: voyage-4, dim=1024) — never OpenAI
  LLM_PROVIDER=claude-cli  → Voyage AI (same as anthropic)
  EMBED_PROVIDER=voyage    → Voyage AI directly

Override the provider with EMBED_PROVIDER (openai | gemini | gemini-vertex | voyage | anthropic | claude-cli).
Override the model name with EMBED_MODEL.
Override the specific API endpoint (for LM Studio / Local nodes) with EMBED_BASE_URL.
Override the specific API key with EMBED_API_KEY (or VOYAGE_API_KEY for Voyage).
Override the output vector size with EMBED_DIMENSIONS.
"""

import asyncio
import hashlib
import json
import logging
import os

from db_layer.embeddings.multimodal import EmbeddingInput

from langchain_core.embeddings import Embeddings

logger = logging.getLogger("whiskers")

# Lazy-initialised embeddings singleton
_embeddings: Embeddings | None = None
# Config version the current singleton was built for (dynamic-pool invalidation).
_embeddings_version: str | None = None
_embeddings_lock: asyncio.Lock = asyncio.Lock()

def _defaults_from_registry(provider: str) -> tuple[str, int]:
    """Resolve (model, dimensions) defaults from each provider module's ProviderSpec."""
    from core.llm_provider_management import default_embed_for
    return default_embed_for(provider)


# Back-compat map for callers that still import `_DEFAULT_MODELS` (e.g. agent.py).
# Prefer `default_embed_for(provider)` — this dict is a thin registry snapshot.
def _snapshot_default_models() -> dict[str, str]:
    from core.llm_provider_management import get_llm_provider_registry
    out: dict[str, str] = {}
    for spec in get_llm_provider_registry().all():
        if spec.default_embed_model:
            out[spec.id] = spec.default_embed_model
    return out


# Lazy property-like module attr: keep a dict for `from ... import _DEFAULT_MODELS`.
_DEFAULT_MODELS: dict[str, str] = {}
try:
    _DEFAULT_MODELS = _snapshot_default_models()
except Exception:
    _DEFAULT_MODELS = {
        "openai": "text-embedding-3-small",
        "gemini": "gemini-embedding-001",
        "voyage": "voyage-4",
        "anthropic": "voyage-4",
        "claude-cli": "voyage-4",
    }


def _make_embeddings() -> Embeddings:
    """Instantiate a LangChain embeddings client from env config."""
    from utils.embedding_config import environment_embedding_selection
    return _make_embeddings_from(environment_embedding_selection())


def _effective_selection(sel: dict) -> dict:
    """Resolve construction options; matching profiles supply batching, never change identity."""
    from utils.embedding_config import EMBEDDING_CONFIG
    from core.llm_provider_management import get_llm_provider_registry
    provider = (sel.get("provider") or "openai").lower()
    model, dims = sel.get("model"), sel.get("dimensions")
    spec = get_llm_provider_registry().get(provider)
    if spec and spec.multimodal_embeddings_factory and dims is not None and type(dims) not in (int, str):
        raise ValueError("multimodal dimensions must be a configured positive integer")
    if not model or not dims:
        default_model, default_dims = _defaults_from_registry(provider)
        model, dims = model or default_model, dims or default_dims
    options = {}
    for profile in EMBEDDING_CONFIG.get("models", {}).values():
        if profile.get("provider") == provider and profile.get("model") == model:
            options = {k: profile[k] for k in ("batch_size", "max_concurrency", "max_media_bytes", "timeout_seconds") if k in profile}
            break
    options.update(sel)
    options.update({
        "provider": provider, "model": model, "dimensions": int(dims),
        "api_key": sel.get("api_key") or os.environ.get("EMBED_API_KEY") or os.environ.get("VOYAGE_API_KEY"),
        "base_url": sel.get("base_url") or os.environ.get("EMBED_BASE_URL") or os.environ.get("VOYAGE_BASE_URL"),
    })
    return options


def _make_embeddings_from(sel: dict) -> Embeddings:
    """Instantiate a registry-resolved text or multimodal client from selection data."""
    from core.llm_provider_management import make_embeddings, get_llm_provider_registry
    effective = _effective_selection(sel)
    kwargs = dict(provider_str=effective["provider"], model=effective["model"],
                  dimensions=effective["dimensions"], api_key=effective["api_key"], base_url=effective["base_url"])
    spec = get_llm_provider_registry().get(effective["provider"])
    if spec and spec.multimodal_embeddings_factory:
        kwargs["options"] = effective
    return make_embeddings(**kwargs)


def _get_embeddings() -> Embeddings:
    """Return the shared embeddings client (env-config, lazily initialised).

    Sync entry point kept for scripts/bulk tools. Runtime graph paths use the
    async, version-aware resolver below.
    """
    global _embeddings
    if _embeddings is None:
        _embeddings = _make_embeddings()
    return _embeddings


async def _get_embeddings_async() -> Embeddings:
    """Return the shared embeddings client, rebuilding it if the active dynamic
    pool selection has changed (version-aware). Falls back to env config."""
    global _embeddings, _embeddings_version
    async with _embeddings_lock:
        sel: dict | None = None
        version: str | None = None
        try:
            from core.llm_config_service import config_version, resolve_embedding
            sel = await resolve_embedding()
            version = await config_version()
        except Exception:
            sel, version = None, None

        if _embeddings is None or (version is not None and version != _embeddings_version):
            _embeddings = _make_embeddings() if sel is None else _make_embeddings_from(sel)
            _embeddings_version = version
        return _embeddings


async def embed(text: str) -> list[float]:
    """Generate an embedding vector for the given text."""
    from utils.embedding_config import format_query
    formatted = format_query(text)
    emb = await _get_embeddings_async()
    return await emb.aembed_query(formatted)


async def embed_batch(texts: list[str], titles: list[str | None] | None = None) -> list[list[float]]:
    """Generate embedding vectors for multiple texts in a single batch request.

    More efficient than calling embed() multiple times as it batches
    API requests and avoids rate limit issues.
    """
    if not texts:
        return []
    from utils.embedding_config import format_document
    if titles:
        clean = [
            format_document(str(t) if t is not None else "", title=titles[i])
            for i, t in enumerate(texts)
        ]
    else:
        clean = [format_document(str(t) if t is not None else "") for t in texts]
    emb = await _get_embeddings_async()
    return await emb.aembed_documents(clean)


_model_clients: dict[str, Embeddings] = {}
_model_clients_lock = asyncio.Lock()


def model_id_for(sel: dict) -> str:
    """Return the canonical model_id string for a resolved selection: provider:model:dimensions."""
    provider = (sel.get("provider") or "openai").lower()
    model, dims = sel.get("model"), sel.get("dimensions")
    if not model or not dims:
        default_model, default_dims = _defaults_from_registry(provider)
        model, dims = model or default_model, dims or default_dims
    return f"{provider}:{model}:{dims}"


async def _get_client_for(sel: dict) -> Embeddings:
    """Get or create the cached embeddings client for a resolved selection."""
    effective = _effective_selection(sel)
    # Vector identity stays provider:model:width; transport cache also separates endpoint,
    # credentials and batching settings. Only a digest of runtime configuration is retained.
    key = hashlib.sha256(json.dumps(effective, sort_keys=True).encode()).hexdigest()
    async with _model_clients_lock:
        if key not in _model_clients:
            if len(_model_clients) >= 32:
                _model_clients.pop(next(iter(_model_clients)))
            _model_clients[key] = _make_embeddings_from(effective)
        return _model_clients[key]


async def embed_multimodal_with(sel: dict, inputs: list[EmbeddingInput]) -> list[list[float]]:
    """Embed typed text/media documents with an explicit registry selection, in input order."""
    if not inputs:
        return []
    from core.llm_provider_management import get_llm_provider_registry
    spec = get_llm_provider_registry().get((sel.get("provider") or "openai").lower())
    if spec is None or spec.multimodal_embeddings_factory is None:
        raise ValueError("Selected embedding provider has no multimodal adapter")
    client = await _get_client_for(sel)
    return await client.aembed_multimodal(inputs)


async def embed_multimodal(inputs: list[EmbeddingInput]) -> list[list[float]]:
    """Embed typed documents using the shared active pool/environment/profile resolution."""
    if not inputs:
        return []
    from core.llm_config_service import resolve_embedding
    return await embed_multimodal_with(await resolve_embedding(), inputs)


async def embed_query_with(sel: dict, text: str) -> list[float]:
    """Generate an embedding vector for a query using the explicit model selection."""
    from utils.embedding_config import format_query
    formatted = format_query(text)
    client = await _get_client_for(sel)
    return await client.aembed_query(formatted)


async def embed_documents_with(sel: dict, texts: list[str], titles: list[str | None] | None = None) -> list[list[float]]:
    """Generate embedding vectors for multiple texts using the explicit model selection."""
    if not texts:
        return []
    from utils.embedding_config import format_document
    if titles:
        clean = [
            format_document(str(t) if t is not None else "", title=titles[i])
            for i, t in enumerate(texts)
        ]
    else:
        clean = [format_document(str(t) if t is not None else "") for t in texts]
    client = await _get_client_for(sel)
    return await client.aembed_documents(clean)
