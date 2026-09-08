"""Add tenant_id to tool_call_events for multi-tenant isolation.

Revision ID: core_038
Revises: core_037
Create Date: 2026-07-10
"""

from alembic import op

revision = "core_038"
down_revision = "core_037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS tenant_id BIGINT"
    )
    op.execute(
        "UPDATE tool_call_events SET tenant_id = 1 WHERE tenant_id IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tool_call_events_tenant_id "
        "ON tool_call_events (tenant_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tool_call_events_tenant_id")
    op.execute("ALTER TABLE tool_call_events DROP COLUMN IF EXISTS tenant_id")
