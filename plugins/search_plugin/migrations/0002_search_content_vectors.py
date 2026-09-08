"""Align search plugin table name to search_content_vectors (idempotent).

core_042 owns the rename for existing installs; this migration ensures the
search table exists for environments that only run plugin migrations.
"""

from __future__ import annotations

import os

from sqlalchemy import text


def upgrade(conn) -> None:
    """Rename content_vectors if present; create search_content_vectors if missing."""
    dim = os.environ.get("EMBED_DIMENSIONS", "1536")
    exists_old = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'content_vectors'"
        )
    ).fetchone()
    exists_new = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'search_content_vectors'"
        )
    ).fetchone()

    if exists_old and not exists_new:
        conn.execute(text("ALTER TABLE content_vectors RENAME TO search_content_vectors"))
        return

    if not exists_new:
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS search_content_vectors (
                    id BIGSERIAL PRIMARY KEY,
                    collection VARCHAR NOT NULL,
                    content_text TEXT NOT NULL,
                    embedding VECTOR({dim}),
                    model VARCHAR NOT NULL,
                    meta JSONB DEFAULT '{{}}' NOT NULL,
                    content_hash VARCHAR NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
                    tenant_id BIGINT,
                    CONSTRAINT search_content_vectors_collection_hash_model_key
                        UNIQUE (collection, content_hash, model)
                )
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
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_search_content_vectors_tenant_id "
                "ON search_content_vectors (tenant_id)"
            )
        )
