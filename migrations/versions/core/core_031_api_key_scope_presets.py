"""Add api_key_scope_presets table for saved custom scope selections.

NULL scopes means legacy full access (same convention as api_keys.scopes).

Revision ID: core_031
Revises: core_030
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "core_031"
down_revision = "core_030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_key_scope_presets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("scopes", JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("api_key_scope_presets")
