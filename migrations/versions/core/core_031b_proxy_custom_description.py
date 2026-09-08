"""Add custom_description column to proxy_servers table.

Revision ID: core_031b
Revises: core_031
Create Date: 2026-06-30
"""

from alembic import op
import sqlalchemy as sa

revision = "core_031b"
down_revision = "core_031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proxy_servers", sa.Column("custom_description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("proxy_servers", "custom_description")
