"""Portfolio plugin — bake observability columns + durable run record.

Promotes composePath / mode / degraded out of layout_json.meta so "what
fraction of bakes fell back to the floor composer" is a SELECT rather than a
JSONB scan, adds updated_at (derived rows are mutated in place with no
invalidation signal today), and creates portfolio_bake_runs — the per-attempt
debug artifact, written for failed bakes too, where short_id stays NULL.

Plugin: portfolio_plugin
Revision: 0005_bake_observability

Applied by PluginSchemaMigrator — receives a live SQLAlchemy connection.
See: db_layer/plugin_schema_migrator.py
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Add bake provenance columns and the portfolio_bake_runs table."""
    for column_ddl in (
        "ADD COLUMN IF NOT EXISTS compose_path TEXT",
        "ADD COLUMN IF NOT EXISTS mode TEXT",
        "ADD COLUMN IF NOT EXISTS degraded BOOLEAN NOT NULL DEFAULT false",
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
    ):
        conn.execute(text(f"ALTER TABLE portfolio_job_layouts {column_ddl}"))

    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_job_layouts_compose_path "
            "ON portfolio_job_layouts (compose_path)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_job_layouts_degraded "
            "ON portfolio_job_layouts (degraded)"
        )
    )

    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS portfolio_bake_runs (
                id BIGSERIAL PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE,
                tenant_id BIGINT NOT NULL,
                short_id TEXT,
                status TEXT NOT NULL,
                compose_path TEXT,
                mode TEXT,
                degraded BOOLEAN NOT NULL DEFAULT false,
                company TEXT,
                role TEXT,
                job_brief_hash TEXT,
                evidence_pack_hash TEXT,
                stages_json JSONB,
                errors_json JSONB,
                total_ms INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_bake_runs_tenant_created "
            "ON portfolio_bake_runs (tenant_id, created_at DESC)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_bake_runs_short_id "
            "ON portfolio_bake_runs (short_id)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_bake_runs_job_brief_hash "
            "ON portfolio_bake_runs (job_brief_hash)"
        )
    )
