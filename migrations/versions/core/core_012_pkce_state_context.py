"""Add context JSONB column to plugin_oauth_pkce_state.

Revision ID: core_012
Revises: core_011
Create Date: 2026-05-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "core_012"
down_revision = "core_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plugin_oauth_pkce_state",
        sa.Column(
            "context",
            JSONB,
            nullable=True,
            comment="Optional JSON blob for the auth flow that owns this PKCE state (e.g. MCP pending context)",
        ),
    )


def downgrade() -> None:
    op.drop_column("plugin_oauth_pkce_state", "context")
