"""Name the embedding queue's existing deduplication constraint.

Revision ID: core_052
Revises: core_051
Create Date: 2026-11-06

core_014 let PostgreSQL name this constraint; producers use embedding_jobs_dedup.
Rename in place so the unique index and queued data are preserved.
"""

from alembic import op
import sqlalchemy as sa

revision = "core_052"
down_revision = "core_051"
branch_labels = None
depends_on = None

_OLD_NAME = "embedding_jobs_plugin_id_operation_id_content_hash_key"
_NEW_NAME = "embedding_jobs_dedup"


def _rename_constraint(old: str, new: str) -> None:
    names = set(op.get_bind().execute(sa.text(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'embedding_jobs'::regclass"
    )).scalars())
    # Scope both guards to the same table resolved by ALTER TABLE's search path.
    if old in names and new not in names:
        op.execute(sa.text(
            f"ALTER TABLE embedding_jobs RENAME CONSTRAINT {old} TO {new}"
        ))


def upgrade() -> None:
    """Rename only when the legacy name exists and the producer name is absent."""
    _rename_constraint(_OLD_NAME, _NEW_NAME)


def downgrade() -> None:
    """Restore the legacy name under the inverse guards."""
    _rename_constraint(_NEW_NAME, _OLD_NAME)
