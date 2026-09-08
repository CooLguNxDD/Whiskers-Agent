"""Add doc_text, search_doc generated tsvector to route_embeddings and workspace_label to proxy_servers.

Revision ID: core_043
Revises: core_042
Create Date: 2026-07-21
"""

from alembic import op

revision = "core_043"
down_revision = "core_042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE route_embeddings ADD COLUMN IF NOT EXISTS doc_text TEXT NULL")
    op.execute(
        "ALTER TABLE route_embeddings ADD COLUMN IF NOT EXISTS search_doc tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(doc_text, ''))) STORED"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_route_embeddings_search_doc ON route_embeddings USING GIN (search_doc)")

    op.execute("ALTER TABLE proxy_servers ADD COLUMN IF NOT EXISTS workspace_label TEXT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_route_embeddings_search_doc")
    op.execute("ALTER TABLE route_embeddings DROP COLUMN IF EXISTS search_doc")
    op.execute("ALTER TABLE route_embeddings DROP COLUMN IF EXISTS doc_text")
    op.execute("ALTER TABLE proxy_servers DROP COLUMN IF EXISTS workspace_label")
