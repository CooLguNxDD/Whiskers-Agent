"""Split content_vectors into search_content_vectors + memory_content_vectors.

Revision ID: core_042
Revises: core_041
Create Date: 2026-07-16

- Rename content_vectors → search_content_vectors (search engine)
- Create memory_content_vectors (core memory / harness RAG)
- Move known memory collections into the memory table
"""

from __future__ import annotations

import os

from alembic import op
from sqlalchemy import text

revision = "core_042"
down_revision = "core_041"
branch_labels = None
depends_on = None

_MEMORY_COLLECTIONS = (
    "global_memory",
    "plan_recipes",
    "plan_anti_patterns",
    "core_instructions",
)


def _dim() -> str:
    return os.environ.get("EMBED_DIMENSIONS", "1536")


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :n"
        ),
        {"n": name},
    ).fetchone()
    return row is not None


def _create_vector_table(conn, table: str, unique_name: str, hnsw: str, tenant_ix: str) -> None:
    dim = _dim()
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id BIGSERIAL PRIMARY KEY,
                collection VARCHAR NOT NULL,
                content_text TEXT NOT NULL,
                embedding VECTOR({dim}),
                model VARCHAR NOT NULL,
                meta JSONB DEFAULT '{{}}'::jsonb,
                content_hash VARCHAR NOT NULL,
                tenant_id BIGINT,
                created_at TIMESTAMPTZ DEFAULT now(),
                CONSTRAINT {unique_name} UNIQUE (collection, content_hash, model)
            )
            """
        )
    )
    conn.execute(
        text(
            f"""
            CREATE INDEX IF NOT EXISTS {hnsw} ON {table}
                USING hnsw (embedding vector_cosine_ops)
                WITH (m=16, ef_construction=64)
            """
        )
    )
    conn.execute(
        text(f"CREATE INDEX IF NOT EXISTS {tenant_ix} ON {table} (tenant_id)")
    )


def upgrade() -> None:
    conn = op.get_bind()

    # --- search_content_vectors (rename or create) ---
    if _table_exists(conn, "content_vectors") and not _table_exists(
        conn, "search_content_vectors"
    ):
        conn.execute(text("ALTER TABLE content_vectors RENAME TO search_content_vectors"))
        # Best-effort constraint/index renames (ignore if names differ)
        for old, new in (
            (
                "content_vectors_collection_hash_model_key",
                "search_content_vectors_collection_hash_model_key",
            ),
        ):
            try:
                conn.execute(
                    text(f"ALTER TABLE search_content_vectors RENAME CONSTRAINT {old} TO {new}")
                )
            except Exception:
                pass
        for old, new in (
            ("content_vectors_hnsw", "search_content_vectors_hnsw"),
            ("ix_content_vectors_tenant_id", "ix_search_content_vectors_tenant_id"),
        ):
            try:
                conn.execute(text(f"ALTER INDEX IF EXISTS {old} RENAME TO {new}"))
            except Exception:
                pass
    elif not _table_exists(conn, "search_content_vectors"):
        _create_vector_table(
            conn,
            "search_content_vectors",
            "search_content_vectors_collection_hash_model_key",
            "search_content_vectors_hnsw",
            "ix_search_content_vectors_tenant_id",
        )

    # --- memory_content_vectors ---
    if not _table_exists(conn, "memory_content_vectors"):
        _create_vector_table(
            conn,
            "memory_content_vectors",
            "memory_content_vectors_collection_hash_model_key",
            "memory_content_vectors_hnsw",
            "ix_memory_content_vectors_tenant_id",
        )

    # --- move memory collections off the search table ---
    if _table_exists(conn, "search_content_vectors") and _table_exists(
        conn, "memory_content_vectors"
    ):
        cols = (
            "collection, content_text, embedding, model, meta, content_hash, "
            "tenant_id, created_at"
        )
        placeholders = ", ".join(f"'{c}'" for c in _MEMORY_COLLECTIONS)
        conn.execute(
            text(
                f"""
                INSERT INTO memory_content_vectors ({cols})
                SELECT {cols}
                FROM search_content_vectors
                WHERE collection IN ({placeholders})
                ON CONFLICT DO NOTHING
                """
            )
        )
        conn.execute(
            text(
                f"""
                DELETE FROM search_content_vectors
                WHERE collection IN ({placeholders})
                """
            )
        )

    # Skeleton harness_instructions row (seeded at runtime from core defaults)
    conn.execute(
        text(
            """
            INSERT INTO server_settings (key, value)
            VALUES (
                'harness_instructions',
                '{"version": 1, "updated_at": null, "seeded": false, "items": []}'::jsonb
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    # Move memory rows back if search table will be renamed to content_vectors
    if _table_exists(conn, "memory_content_vectors") and _table_exists(
        conn, "search_content_vectors"
    ):
        cols = (
            "collection, content_text, embedding, model, meta, content_hash, "
            "tenant_id, created_at"
        )
        placeholders = ", ".join(f"'{c}'" for c in _MEMORY_COLLECTIONS)
        conn.execute(
            text(
                f"""
                INSERT INTO search_content_vectors ({cols})
                SELECT {cols}
                FROM memory_content_vectors
                WHERE collection IN ({placeholders})
                ON CONFLICT DO NOTHING
                """
            )
        )

    if _table_exists(conn, "search_content_vectors") and not _table_exists(
        conn, "content_vectors"
    ):
        conn.execute(text("ALTER TABLE search_content_vectors RENAME TO content_vectors"))

    if _table_exists(conn, "memory_content_vectors"):
        conn.execute(text("DROP TABLE IF EXISTS memory_content_vectors"))

    conn.execute(
        text("DELETE FROM server_settings WHERE key = 'harness_instructions'")
    )
