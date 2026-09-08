"""Add is_enabled flag to route_embeddings for per-plugin toggle.

Revision ID: core_015
Revises: core_014
Create Date: 2026-05-22

Adds ``is_enabled BOOLEAN NOT NULL DEFAULT TRUE`` to ``route_embeddings`` so
operators can disable a plugin's routes from semantic search without deleting
embedding data.  Mirrors the ``is_active`` toggle on the ``plugins`` table.
"""

from alembic import op

revision = "core_015"
down_revision = "core_014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE route_embeddings
            ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN NOT NULL DEFAULT TRUE
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS route_embeddings_enabled_idx
            ON route_embeddings (plugin_id, is_enabled)
    """)


def downgrade() -> None:
    import sqlalchemy as sa
    conn = op.get_bind()
    table_exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'route_embeddings')"
    )).scalar()

    if table_exists:
        op.execute("DROP INDEX IF EXISTS route_embeddings_enabled_idx")
        op.execute(
            "ALTER TABLE route_embeddings DROP COLUMN IF EXISTS is_enabled"
        )
