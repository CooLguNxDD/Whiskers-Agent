"""Portfolio plugin — portfolio_job_layouts table (idempotent).

Job-specific pre-baked layout artifacts keyed by a short URL-safe id
(see utils/short_id.py), served read-only/publicly via
GET /api/portfolio/public/layout/{job_id} — no LLM call at read time.

Plugin: portfolio_plugin
Revision: 0002_job_layouts

Applied by PluginSchemaMigrator — receives a live SQLAlchemy connection.
See: db_layer/plugin_schema_migrator.py
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Create portfolio_job_layouts + indexes if missing."""
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS portfolio_job_layouts (
                id BIGSERIAL PRIMARY KEY,
                short_id VARCHAR NOT NULL UNIQUE,
                job_application_job_id VARCHAR,
                tenant_id BIGINT,
                audience VARCHAR NOT NULL,
                star_query TEXT,
                layout_json JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now() NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_job_layouts_short_id "
            "ON portfolio_job_layouts (short_id)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_job_layouts_job_application_job_id "
            "ON portfolio_job_layouts (job_application_job_id)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_job_layouts_tenant_id "
            "ON portfolio_job_layouts (tenant_id)"
        )
    )
