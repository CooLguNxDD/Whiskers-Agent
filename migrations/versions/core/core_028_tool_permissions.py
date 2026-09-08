"""Add tool_permissions table for read/write policy gate.

Revision ID: core_028
Revises: core_027
Create Date: 2026-06-17

Sparse table: absent (plugin_id, operation_id) row means default policy:
allow_read=TRUE, allow_write=FALSE, require_confirmation=TRUE.
"""

from alembic import op
import sqlalchemy as sa

revision = "core_028"
down_revision = "core_027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_permissions",
        sa.Column("plugin_id", sa.Text(), nullable=False),
        sa.Column("operation_id", sa.Text(), nullable=False),
        sa.Column("allow_read", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("allow_write", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("require_confirmation", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("plugin_id", "operation_id"),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS tool_permissions_plugin_idx ON tool_permissions (plugin_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS tool_permissions_plugin_idx")
    op.drop_table("tool_permissions")
