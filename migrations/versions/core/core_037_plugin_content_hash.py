"""Add plugins.content_hash column (source-tree / proxy identity hash).

Backfills from legacy meta->>'content_hash' when present so existing
version-control data survives the move out of JSONB.

Revision ID: core_037
Revises: core_036
Create Date: 2026-07-10
"""

from alembic import op
import sqlalchemy as sa

revision = "core_037"
down_revision = "core_036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plugins",
        sa.Column(
            "content_hash",
            sa.Text(),
            nullable=True,
            comment="sha256:<hex> full source-tree or proxy identity hash",
        ),
    )
    # Backfill from meta JSONB written by the pre-column implementation.
    op.execute(
        """
        UPDATE plugins
        SET content_hash = meta->>'content_hash'
        WHERE content_hash IS NULL
          AND meta ? 'content_hash'
          AND COALESCE(meta->>'content_hash', '') <> ''
        """
    )
    # Drop the legacy meta key so the column is the single source of truth.
    op.execute(
        """
        UPDATE plugins
        SET meta = meta - 'content_hash'
        WHERE meta ? 'content_hash'
        """
    )


def downgrade() -> None:
    # Restore meta key from the column before dropping it.
    op.execute(
        """
        UPDATE plugins
        SET meta = jsonb_set(
            COALESCE(meta, '{}'::jsonb),
            '{content_hash}',
            to_jsonb(content_hash)
        )
        WHERE content_hash IS NOT NULL
          AND content_hash <> ''
        """
    )
    op.drop_column("plugins", "content_hash")
