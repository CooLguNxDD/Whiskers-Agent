"""Create plugins registry table.

Revision ID: core_002
Revises: core_001
Create Date: 2026-04-14

Stores plugin manifests including capabilities, required credentials, and
external OAuth provider declarations. Updated on every plugin load.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TEXT

revision = "core_002"
down_revision = "core_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugins",
        sa.Column("id", sa.Text(), primary_key=True, comment="Plugin package name e.g. 'plugins.search_plugin'"),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("tier", sa.Text(), nullable=False, server_default="free"),
        sa.Column("capabilities", ARRAY(TEXT), nullable=False, server_default="{}"),
        sa.Column("required_credentials", ARRAY(TEXT), nullable=False, server_default="{}",
                  comment="Credential key names this plugin needs from plugin_credentials"),
        sa.Column("external_oauth_providers", ARRAY(TEXT), nullable=False, server_default="{}",
                  comment="Provider names this plugin needs external OAuth tokens for"),
        sa.Column("meta", JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("registered_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("plugins")
