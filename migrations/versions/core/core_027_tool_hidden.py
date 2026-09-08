"""Add is_hidden to tool_config for gateway (run_graph unified) mode.

Revision ID: core_027
Revises: core_026
Create Date: 2026-06-17

``is_hidden`` is distinct from ``is_enabled``: a hidden tool is removed from
``mcp.list_tools()`` and rejects direct ``call_tool`` (like disabled), but stays
fully reachable internally by the ``run_graph`` gateway (which executes via the
route registry / fast-path, not FastMCP ``call_tool``). One platform tool =
unlimited tools behind the scenes. Sparse — absence means visible.
"""

from alembic import op

revision = "core_027"
down_revision = "core_026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tool_config ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS tool_config_hidden_idx ON tool_config (is_hidden)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS tool_config_hidden_idx")
    op.execute("ALTER TABLE tool_config DROP COLUMN IF EXISTS is_hidden")
