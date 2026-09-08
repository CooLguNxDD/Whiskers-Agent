"""Add scopes column to api_keys for per-key access control.

NULL means legacy full access (preserves existing keys' behavior).

Revision ID: core_030
Revises: core_029
Create Date: 2026-06-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "core_030"
down_revision = "core_029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("scopes", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "scopes")