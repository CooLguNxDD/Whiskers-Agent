"""Portfolio plugin — portfolio_projects table (idempotent).

Plugin: portfolio_plugin
Revision: 0001_init

Applied by PluginSchemaMigrator — receives a live SQLAlchemy connection.
See: db_layer/plugin_schema_migrator.py
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Create portfolio_projects + tenant_id / context_sources seams."""
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS portfolio_projects (
                id BIGSERIAL PRIMARY KEY,
                slug VARCHAR NOT NULL UNIQUE,
                name VARCHAR NOT NULL,
                summary TEXT NOT NULL,
                tags JSONB DEFAULT '[]' NOT NULL,
                metrics JSONB DEFAULT '[]' NOT NULL,
                links JSONB DEFAULT '[]' NOT NULL,
                audiences JSONB DEFAULT '[]' NOT NULL,
                sort_order INTEGER DEFAULT 0 NOT NULL,
                is_active BOOLEAN DEFAULT true NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
            )
            """
        )
    )
    conn.execute(
        text("ALTER TABLE portfolio_projects ADD COLUMN IF NOT EXISTS tenant_id BIGINT")
    )
    # Backfill after column is visible (no DO $$ — plain DML is fine here).
    conn.execute(
        text("UPDATE portfolio_projects SET tenant_id = 1 WHERE tenant_id IS NULL")
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_projects_tenant_id "
            "ON portfolio_projects (tenant_id)"
        )
    )
    conn.execute(
        text(
            """
            ALTER TABLE portfolio_projects
                ADD COLUMN IF NOT EXISTS context_sources JSONB NOT NULL DEFAULT '[]'::jsonb
            """
        )
    )
