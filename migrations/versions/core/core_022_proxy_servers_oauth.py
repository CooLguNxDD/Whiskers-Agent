"""Add auth_mode and oauth_config columns to proxy_servers table.

Revision ID: core_022
Revises: core_021
Create Date: 2026-06-01
"""

from alembic import op

revision = "core_022"
down_revision = "core_021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE proxy_servers ADD COLUMN IF NOT EXISTS auth_mode TEXT NOT NULL DEFAULT 'none'")
    op.execute("ALTER TABLE proxy_servers ADD COLUMN IF NOT EXISTS oauth_config JSONB")
    
    # Migrate has_auth values to auth_mode
    op.execute("UPDATE proxy_servers SET auth_mode = 'bearer' WHERE has_auth = TRUE")


def downgrade() -> None:
    op.execute("ALTER TABLE proxy_servers DROP COLUMN IF EXISTS oauth_config")
    op.execute("ALTER TABLE proxy_servers DROP COLUMN IF EXISTS auth_mode")
