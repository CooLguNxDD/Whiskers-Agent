"""Add mcp_pending_auths table for Layer 1 restart-safe auth state.

Revision ID: core_013
Revises: core_012
Create Date: 2026-05-08
"""
from alembic import op

revision = "core_013"
down_revision = "core_012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_pending_auths (
            auth_state  TEXT PRIMARY KEY,
            data        TEXT NOT NULL,
            expires_at  TIMESTAMPTZ NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mcp_pending_auths")
