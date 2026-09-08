"""Add tenant_id columns to all core and plugin models.

Revision ID: core_036
Revises: core_035
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa

revision = "core_036"
down_revision = "core_035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Core tables (always exist in schema, but may already have column from a partial run)
    core_tables = ["api_keys", "api_key_scope_presets", "workflow_executions"]
    for table in core_tables:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id BIGINT")
        op.execute(f"UPDATE {table} SET tenant_id = 1 WHERE tenant_id IS NULL")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant_id ON {table} (tenant_id)")

    # 2. Plugin tables (may or may not exist depending on active plugins / branch)
    plugin_tables = [
        "content_vectors",
        "job_applicant_profiles",
        "job_applications",
        "job_preference_embeddings",
    ]
    for table in plugin_tables:
        op.execute(f"""
            DO $$
            BEGIN
                IF to_regclass('{table}') IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id BIGINT';
                    EXECUTE 'UPDATE {table} SET tenant_id = 1 WHERE tenant_id IS NULL';
                    EXECUTE 'CREATE INDEX IF NOT EXISTS ix_{table}_tenant_id ON {table} (tenant_id)';
                END IF;
            END
            $$;
        """)


def downgrade() -> None:
    # 1. Core tables
    core_tables = ["api_keys", "api_key_scope_presets", "workflow_executions"]
    for table in core_tables:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_tenant_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS tenant_id")

    # 2. Plugin tables
    plugin_tables = [
        "content_vectors",
        "job_applicant_profiles",
        "job_applications",
        "job_preference_embeddings",
    ]
    for table in plugin_tables:
        op.execute(f"""
            DO $$
            BEGIN
                IF to_regclass('{table}') IS NOT NULL THEN
                    EXECUTE 'DROP INDEX IF EXISTS ix_{table}_tenant_id';
                    EXECUTE 'ALTER TABLE {table} DROP COLUMN IF EXISTS tenant_id';
                END IF;
            END
            $$;
        """)
