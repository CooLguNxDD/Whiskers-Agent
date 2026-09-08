"""Create proxy_servers table for upstream MCP proxies.

Revision ID: core_018
Revises: core_017
Create Date: 2026-05-29
"""

from alembic import op

revision = "core_018"
down_revision = "core_017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS proxy_servers (
            id            UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            name          TEXT          NOT NULL UNIQUE,
            transport     TEXT          NOT NULL,
            url           TEXT          NOT NULL,
            status        TEXT          NOT NULL DEFAULT 'active',
            has_auth      BOOLEAN       NOT NULL DEFAULT FALSE,
            tool_count    INTEGER       NOT NULL DEFAULT 0,
            error_message TEXT,
            created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS proxy_servers_name_idx ON proxy_servers (name)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS proxy_servers_name_idx")
    op.execute("DROP TABLE IF EXISTS proxy_servers")
