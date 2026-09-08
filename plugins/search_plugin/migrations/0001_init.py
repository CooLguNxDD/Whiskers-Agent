"""Search plugin — content_vectors table + HNSW index.

Idempotent: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
Uses EMBED_DIMENSIONS for VECTOR column width.
"""

from __future__ import annotations

import os

from sqlalchemy import text


def upgrade(conn) -> None:
    """Create content_vectors (+ tenant seam) if missing."""
    dim = os.environ.get("EMBED_DIMENSIONS", "1536")
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS content_vectors (
                id BIGSERIAL PRIMARY KEY,
                collection VARCHAR NOT NULL,
                content_text TEXT NOT NULL,
                embedding VECTOR({dim}),
                model VARCHAR NOT NULL,
                meta JSONB DEFAULT '{{}}' NOT NULL,
                content_hash VARCHAR NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
                CONSTRAINT content_vectors_collection_hash_model_key
                    UNIQUE (collection, content_hash, model)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS content_vectors_hnsw ON content_vectors
                USING hnsw (embedding vector_cosine_ops)
                WITH (m=16, ef_construction=64)
            """
        )
    )
    conn.execute(
        text("ALTER TABLE content_vectors ADD COLUMN IF NOT EXISTS tenant_id BIGINT")
    )
    conn.execute(
        text("UPDATE content_vectors SET tenant_id = 1 WHERE tenant_id IS NULL")
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_content_vectors_tenant_id "
            "ON content_vectors (tenant_id)"
        )
    )
