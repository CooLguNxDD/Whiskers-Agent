"""Create tool_config table for per-tool MCP-exposure toggle.

Revision ID: core_017
Revises: core_016
Create Date: 2026-05-27

Stores whether an individual MCP tool is exposed to clients. Distinct from
``route_embeddings.is_enabled`` (which only gates semantic-search routing and
exists only when RAG/embeddings is on). This table gates FastMCP registration
itself and works regardless of RAG. Sparse: only disabled tools need a row —
absence means enabled.
"""

from alembic import op

revision = "core_017"
down_revision = "core_016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_config (
            plugin_id  TEXT        NOT NULL,
            tool_name  TEXT        NOT NULL,
            is_enabled BOOLEAN     NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (plugin_id, tool_name)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS tool_config_disabled_idx
            ON tool_config (is_enabled)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS tool_config_disabled_idx")
    op.execute("DROP TABLE IF EXISTS tool_config")
