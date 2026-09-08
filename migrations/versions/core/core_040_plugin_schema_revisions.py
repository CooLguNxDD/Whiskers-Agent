"""Ledger table for per-plugin modular schema migrations.

Revision ID: core_040
Revises: core_039
Create Date: 2026-07-13

plugin_schema_revisions records which plugin-local migration steps have been
applied (keyed by plugin_id + revision). Replaces Alembic plugin branches as
the source of truth for plugin DDL after Phase 1 of the marketplace work.
"""

from alembic import op

revision = "core_040"
down_revision = "core_039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_schema_revisions (
            plugin_id      VARCHAR NOT NULL,
            revision       VARCHAR NOT NULL,
            checksum       VARCHAR NOT NULL,
            plugin_version VARCHAR,
            applied_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            execution_ms   INTEGER,
            PRIMARY KEY (plugin_id, revision)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS plugin_schema_revisions")
