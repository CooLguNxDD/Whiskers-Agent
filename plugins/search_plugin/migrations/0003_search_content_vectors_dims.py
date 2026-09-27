"""Re-type search_content_vectors.embedding to the live embedding width, if it drifted.

0001/0002 fix the width only when the table is first created; a table that
already exists (or was renamed from content_vectors) keeps its old width when
the embedding provider changes. This step compares the declared width with
``embedding_dimensions()`` and is a no-op when they match.

On a mismatch the stored rows are unusable (wrong width). They are a search
cache, rebuilt by the next ``semantic_index`` call, so they are dropped.
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Resize the embedding column and rebuild its HNSW index when the width changed."""
    from core.embedding_dimensions import embedding_dimensions, vector_column_width

    dim = embedding_dimensions()
    current = vector_column_width(conn, "search_content_vectors")
    if current is None or current == dim:
        return

    # A plugin-only rename (0002) keeps the old content_vectors_hnsw name.
    conn.execute(text("DROP INDEX IF EXISTS search_content_vectors_hnsw"))
    conn.execute(text("DROP INDEX IF EXISTS content_vectors_hnsw"))
    conn.execute(text("DELETE FROM search_content_vectors"))
    conn.execute(
        text(
            f"""
            ALTER TABLE search_content_vectors
            ALTER COLUMN embedding TYPE vector({dim})
            USING NULL
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS search_content_vectors_hnsw
                ON search_content_vectors
                USING hnsw (embedding vector_cosine_ops)
                WITH (m=16, ef_construction=64)
            """
        )
    )
