"""Add feature/parent_run_id to tool_call_events and create graph_run_events.

Splits the telemetry product axis into `mcp` (individual tool invocations) vs
`graph` (whole agent runs), per the telemetry-implementation-report verification
(.claude/pendingPlan/telemetry-implementation-report/). Existing rows default to
`feature='mcp'` so the current dashboard keeps working unmodified.

Revision ID: core_049
Revises: core_048
Create Date: 2026-08-27
"""

from alembic import op

revision = "core_049"
down_revision = "core_048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add feature/parent_run_id to tool_call_events and create graph_run_events."""
    op.execute(
        "ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS feature TEXT NOT NULL DEFAULT 'mcp'"
    )
    op.execute(
        "ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS parent_run_id TEXT"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tool_call_events_parent_run_id "
        "ON tool_call_events (parent_run_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_run_events (
            id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL,
            tenant_id BIGINT NOT NULL,
            subject TEXT,
            mode TEXT,
            flow_id TEXT,
            goal_class TEXT,
            ok BOOLEAN NOT NULL,
            error_type TEXT,
            terminal_status TEXT,
            latency_ms INTEGER NOT NULL,
            step_count INTEGER,
            failed_steps INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_graph_run_events_run_id ON graph_run_events (run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_graph_run_events_tenant_created "
        "ON graph_run_events (tenant_id, created_at DESC)"
    )


def downgrade() -> None:
    """Drop graph_run_events and the feature/parent_run_id columns on tool_call_events."""
    op.execute("DROP TABLE IF EXISTS graph_run_events")
    op.execute("DROP INDEX IF EXISTS ix_tool_call_events_parent_run_id")
    op.execute("ALTER TABLE tool_call_events DROP COLUMN IF EXISTS parent_run_id")
    op.execute("ALTER TABLE tool_call_events DROP COLUMN IF EXISTS feature")
