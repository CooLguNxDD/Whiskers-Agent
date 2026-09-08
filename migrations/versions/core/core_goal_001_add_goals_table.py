"""Add goals table

Revision ID: core_goal_001
Revises: 
Create Date: 2026-06-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "core_goal_001"
down_revision = "core_023"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("raw_goal", sa.Text(), nullable=False),
        sa.Column("goal_spec", JSONB(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

def downgrade() -> None:
    op.drop_table("goals")
