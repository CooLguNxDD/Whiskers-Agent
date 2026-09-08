"""Add generated search_doc tsvector + GIN index to unity_world_vectors so the
unified search engine (db_layer/embeddings/search_engine.py) can run hybrid
dense+sparse search on it, same as core_044 did for the core-owned embedding
tables. Plugin-local (not core Alembic) because unity_world_vectors is
world_semantic_plugin-owned and doesn't exist until this plugin's own
migration 0004 has run.
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    conn.execute(
        text(
            """
            ALTER TABLE unity_world_vectors ADD COLUMN IF NOT EXISTS search_doc tsvector
                GENERATED ALWAYS AS (to_tsvector('english', coalesce(content_text, ''))) STORED
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_unity_world_vectors_search_doc
            ON unity_world_vectors USING GIN (search_doc)
            """
        )
    )
