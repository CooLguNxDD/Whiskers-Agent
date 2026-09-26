"""Re-type unity_world_vectors.embedding to the live EMBED_DIMENSIONS.

0005 already did this once, but plugin migrations are checksum-gated: a later
EMBED_DIMENSIONS change does not re-run 0005. This step repeats the same
safe rewrite so a width drift after 0005 still lands on vector({dim}).
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    from core.embedding_dimensions import embedding_dimensions
    dim = embedding_dimensions()
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
