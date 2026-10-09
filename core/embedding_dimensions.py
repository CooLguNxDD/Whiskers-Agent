"""Environment embedding defaults shared by runtime code and migrations."""

import logging
import os

from sqlalchemy import text

logger = logging.getLogger("whiskers.embedding_dimensions")


def embedding_dimensions() -> int:
    """Resolve the configured width, falling back to the provider's default.

    Legacy text providers fall back on invalid ``EMBED_DIMENSIONS``. Opt-in
    multimodal profiles require a configured positive width; none is assumed.
    """
    from core.llm_provider_management import default_embed_for

    from utils.embedding_config import get_active_profile
    from core.llm_provider_management import get_llm_provider_registry
    profile = get_active_profile()
    spec = get_llm_provider_registry().get(profile.get("provider", ""))
    if spec and spec.multimodal_embeddings_factory is not None:
        from utils.embedding_config import environment_embedding_selection
        return int(environment_embedding_selection()["dimensions"])

    provider = os.environ.get("EMBED_PROVIDER", os.environ.get("LLM_PROVIDER", "openai"))
    raw = os.environ.get("EMBED_DIMENSIONS", "").strip()
    if raw:
        try:
            dim = int(raw)
        except ValueError:
            dim = 0
        if dim > 0:
            return dim
        logger.warning("EMBED_DIMENSIONS=%r is not a positive integer; using provider default", raw)
    return int(default_embed_for(provider)[1])


def vector_column_width(conn, table: str, column: str = "embedding") -> int | None:
    """Declared pgvector width of ``table.column`` (sync conn), or None if absent/untyped."""
    row = conn.execute(
        text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = to_regclass(:t) AND attname = :c AND NOT attisdropped"
        ),
        {"t": table, "c": column},
    ).fetchone()
    # pgvector stores the dimension directly in atttypmod; -1 means unconstrained.
    if row is None or row[0] is None or row[0] < 1:
        return None
    return int(row[0])
