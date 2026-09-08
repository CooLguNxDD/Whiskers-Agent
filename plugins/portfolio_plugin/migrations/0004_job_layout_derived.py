"""Portfolio plugin — derived (chat-patched) job layout columns.

Original bake rows stay immutable; only is_derived=true may be updated.
parent_short_id points at the original HR-facing bake short_id.

Plugin: portfolio_plugin
Revision: 0004_job_layout_derived

Applied by PluginSchemaMigrator — receives a live SQLAlchemy connection.
See: db_layer/plugin_schema_migrator.py
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Add parent_short_id / is_derived + index if missing."""
    conn.execute(
        text(
            "ALTER TABLE portfolio_job_layouts "
            "ADD COLUMN IF NOT EXISTS parent_short_id TEXT"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE portfolio_job_layouts "
            "ADD COLUMN IF NOT EXISTS is_derived BOOLEAN NOT NULL DEFAULT false"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_job_layouts_parent_short_id "
            "ON portfolio_job_layouts (parent_short_id)"
        )
    )
