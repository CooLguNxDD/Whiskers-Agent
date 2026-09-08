"""Portfolio plugin — durable provenance columns on portfolio_job_layouts.

Queryable ground truth for "what did we ship for this job" (plan_json,
recipe_id, jury_score). Idempotent ADD COLUMN IF NOT EXISTS.

Plugin: portfolio_plugin
Revision: 0003_job_layout_provenance

Applied by PluginSchemaMigrator — receives a live SQLAlchemy connection.
See: db_layer/plugin_schema_migrator.py
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Add plan_json / recipe_id / jury_score if missing."""
    conn.execute(
        text(
            "ALTER TABLE portfolio_job_layouts "
            "ADD COLUMN IF NOT EXISTS plan_json JSONB"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE portfolio_job_layouts "
            "ADD COLUMN IF NOT EXISTS recipe_id VARCHAR"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE portfolio_job_layouts "
            "ADD COLUMN IF NOT EXISTS jury_score DOUBLE PRECISION"
        )
    )
