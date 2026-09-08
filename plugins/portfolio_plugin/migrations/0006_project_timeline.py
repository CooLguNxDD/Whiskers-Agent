"""Portfolio plugin — project timeline columns.

Adds started_on / ended_on / timeline_source to portfolio_projects so
discovery-parsed "Period Covered" text (Notion) and repo activity dates
(GitHub) survive as typed columns instead of being discarded as README
chrome. ended_on IS NULL means ongoing, not unknown; started_on IS NULL
means unknown. timeline_source in {"notion_period","github","manual"}
drives reconcile precedence (notion_period beats github) and the
operator-lock (manual is never overwritten by discovery).

Plugin: portfolio_plugin
Revision: 0006_project_timeline

Applied by PluginSchemaMigrator — receives a live SQLAlchemy connection.
See: db_layer/plugin_schema_migrator.py
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Add project timeline columns and a lookup index."""
    for column_ddl in (
        "ADD COLUMN IF NOT EXISTS started_on DATE",
        "ADD COLUMN IF NOT EXISTS ended_on DATE",
        "ADD COLUMN IF NOT EXISTS timeline_source TEXT",
    ):
        conn.execute(text(f"ALTER TABLE portfolio_projects {column_ddl}"))

    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_projects_ended_on "
            "ON portfolio_projects (ended_on DESC NULLS FIRST)"
        )
    )
