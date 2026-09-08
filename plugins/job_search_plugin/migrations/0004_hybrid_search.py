"""Job search plugin — search_doc generated tsvector + GIN index on job_preference_embeddings.

Idempotent.
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Add generated search_doc column and GIN index if missing."""
    conn.execute(text("""
        ALTER TABLE job_preference_embeddings
        ADD COLUMN IF NOT EXISTS search_doc tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_job_pref_search_doc "
        "ON job_preference_embeddings USING gin(search_doc)"
    ))
