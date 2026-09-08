"""Add strength column to llm_pool.

Revision ID: core_021
Revises: core_020
Create Date: 2026-05-29
"""

from alembic import op

revision = "core_021"
down_revision = "core_020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE llm_pool ADD COLUMN IF NOT EXISTS strength DOUBLE PRECISION NOT NULL DEFAULT 1.0")


def downgrade() -> None:
    op.execute("ALTER TABLE llm_pool DROP COLUMN IF EXISTS strength")
