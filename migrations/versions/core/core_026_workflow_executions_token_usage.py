"""add token usage to workflow executions

Revision ID: core_026
Revises: core_025
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "core_026"
down_revision = "core_025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_executions", sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("workflow_executions", sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("workflow_executions", sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("workflow_executions", sa.Column("model_usage", JSONB(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("workflow_executions", "model_usage")
    op.drop_column("workflow_executions", "total_tokens")
    op.drop_column("workflow_executions", "output_tokens")
    op.drop_column("workflow_executions", "input_tokens")
