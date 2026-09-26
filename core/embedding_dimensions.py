"""Environment embedding defaults shared by runtime code and migrations."""

import os


def embedding_dimensions() -> int:
    """Resolve the configured width, falling back to the provider's default."""
    from core.llm_provider_management import default_embed_for

    provider = os.environ.get("EMBED_PROVIDER", os.environ.get("LLM_PROVIDER", "openai"))
    return int(os.environ.get("EMBED_DIMENSIONS", "") or default_embed_for(provider)[1])
