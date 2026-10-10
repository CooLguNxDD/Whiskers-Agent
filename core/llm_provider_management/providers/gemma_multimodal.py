"""Registry entry for the explicit LiteLLM content-parts adapter, not EmbeddingGemma."""

from core.llm_provider_management.registry import register_provider
from core.llm_provider_management.spec import ProviderSpec


def _embeddings(model, dimensions, api_key, base_url, options):
    """Build a lazy multimodal client; batching options come from selection/profile data."""
    from db_layer.embeddings.multimodal import GemmaMultimodalEmbeddings
    allowed = {"batch_size", "max_concurrency", "max_media_bytes", "timeout_seconds"}
    return GemmaMultimodalEmbeddings(model, dimensions, api_key, base_url,
                                    **{k: v for k, v in options.items() if k in allowed})


SPEC = ProviderSpec(
    id="gemma-multimodal",
    env_key="EMBED_API_KEY",
    multimodal_embeddings_factory=_embeddings,
    embedding_modalities=("text", "image/png", "image/jpeg", "audio/wav"),
)
register_provider(SPEC)
