"""Create workflow_executions table and add session_id to workflow_plans

Revision ID: core_025
Revises: core_024
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "core_025"
down_revision = "core_024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add session_id to workflow_plans
    op.add_column("workflow_plans", sa.Column("session_id", sa.Text(), nullable=True))
    op.create_index("ix_workflow_plans_session_id", "workflow_plans", ["session_id"])

    # 2. Create workflow_executions table
    op.create_table(
        "workflow_executions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", sa.Text(), nullable=True, index=True),
        sa.Column(
            "workflow_plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workflow_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("user_query", sa.Text(), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_results", JSONB(), nullable=False, server_default="[]"),
        sa.Column("working_memory", JSONB(), nullable=False, server_default="{}"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("carry", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    # 1. Drop workflow_executions table
    op.drop_table("workflow_executions")

    # 2. Drop session_id from workflow_plans
    op.drop_index("ix_workflow_plans_session_id", table_name="workflow_plans")
    op.drop_column("workflow_plans", "session_id")
