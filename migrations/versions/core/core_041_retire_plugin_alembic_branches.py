"""Retire Alembic plugin branch heads from alembic_version.

Revision ID: core_041
Revises: core_040
Create Date: 2026-07-13

After relocating first-party plugin DDL to plugin-local migrations/
(PluginSchemaMigrator + plugin_schema_revisions), remove the old multi-head
plugin branch rows so ``alembic current`` shows only the core chain.
"""

from alembic import op

revision = "core_041"
down_revision = "core_040"
branch_labels = None
depends_on = None

# All known plugin-branch revision ids (heads + intermediates).
_PLUGIN_REVISIONS = (
    "pro_001",
    "opencat_001",
    "opencat_002",
    "job_search_001",
    "search_001",
    "opencat_001",
    "opencat_002",
    "portfolio_001",
)


def upgrade() -> None:
    # Bind-parameter list: delete any residual plugin branch stamps.
    placeholders = ", ".join(f"'{r}'" for r in _PLUGIN_REVISIONS)
    op.execute(
        f"DELETE FROM alembic_version WHERE version_num IN ({placeholders})"
    )


def downgrade() -> None:
    # Cannot restore multi-head history safely; no-op.
    pass
