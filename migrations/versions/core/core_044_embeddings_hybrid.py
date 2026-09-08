"""Add generated search_doc tsvector + GIN index to the remaining core-owned
embedding tables (memory_content_vectors, search_content_vectors) so the
unified search engine (db_layer/embeddings/search_engine.py) can run hybrid
dense+sparse search on them the same way route_embeddings already does
(core_043).

unity_world_vectors is plugin-owned (world_semantic_plugin) and gets the same
treatment via its own plugin-local migration instead — see
plugins/world_semantic_plugin/migrations/0007_hybrid_search_doc.py — since
that table doesn't exist yet when core migrations run (plugin schema is
applied later, on plugin load).

GENERATED ALWAYS ... STORED auto-populates from each table's existing text
column — no data backfill / re-embedding required (sparse is lexical FTS,
not vector).

Revision ID: core_044
Revises: core_043
Create Date: 2026-07-22
"""

from alembic import op

revision = "core_044"
down_revision = "core_043"
branch_labels = None
depends_on = None

# (table, source text column) — all core-owned, always present by this point
# in the migration chain.
_TABLES = [
    ("memory_content_vectors", "content_text"),
    ("search_content_vectors", "content_text"),
]


def upgrade() -> None:
    for table, text_col in _TABLES:
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS search_doc tsvector "
            f"GENERATED ALWAYS AS (to_tsvector('english', coalesce({text_col}, ''))) STORED"
        )
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_search_doc ON {table} USING GIN (search_doc)"
        )


def downgrade() -> None:
    for table, _text_col in reversed(_TABLES):
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_search_doc")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS search_doc")
