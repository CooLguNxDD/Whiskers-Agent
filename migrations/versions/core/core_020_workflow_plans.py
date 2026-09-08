"""Create workflow_plans table — audit log + reuse store for YAML workflows.

Every dynamic-graph run compiles the planner's instruction set into a YAML
workflow; the generated YAML, the compiled ExecutionStep[] plan, and the model
metadata are persisted here for logging/audit and future embedding-based reuse.

Revision ID: core_020
Revises: core_019
Create Date: 2026-05-29
"""

from alembic import op

revision = "core_020"
down_revision = "core_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_plans (
            id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            name             TEXT         NOT NULL,
            user_query       TEXT         NOT NULL,
            yaml_content     TEXT         NOT NULL,
            compiled_plan    JSONB        NOT NULL DEFAULT '[]',
            outputs          JSONB        NOT NULL DEFAULT '{}',
            llm_provider     VARCHAR(32)  NOT NULL,
            llm_model        VARCHAR(128) NOT NULL,
            embed_model      VARCHAR(128),
            status           VARCHAR(32)  NOT NULL DEFAULT 'generated',
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
            last_executed_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS workflow_plans_created_idx ON workflow_plans (created_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS workflow_plans_created_idx")
    op.execute("DROP TABLE IF EXISTS workflow_plans")
