"""Re-type unity_world_vectors.embedding to global EMBED_DIMENSIONS.

0004 created VECTOR(dim) from env at first migrate. If EMBED_DIMENSIONS later
matches the real model (e.g. embeddinggemma → 768) but the column still has
an old fixed size (e.g. 1500), inserts fail with:
  expected N dimensions, not M

This migration rewrites the column to VECTOR(EMBED_DIMENSIONS) — same source
as route_embeddings / contact_embeddings / .env_sample embed config.
"""

from __future__ import annotations

import os

from sqlalchemy import text


def upgrade(conn) -> None:
    dim = int(os.environ.get("EMBED_DIMENSIONS", "") or 1536)
    if dim < 1:
        dim = 1536

    # Existing rows may be wrong-width; drop them (RAG can re-enqueue).
    conn.execute(text("DELETE FROM unity_world_vectors"))

    # Drop HNSW/IVF if any future index depends on fixed dim.
    conn.execute(text("DROP INDEX IF EXISTS unity_world_vectors_embedding_hnsw"))
    conn.execute(text("DROP INDEX IF EXISTS ix_unity_world_vectors_embedding"))

    conn.execute(
        text(
            f"""
            ALTER TABLE unity_world_vectors
            ALTER COLUMN embedding TYPE vector({dim})
            USING NULL
            """
        )
    )
