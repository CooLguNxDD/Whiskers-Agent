"""Create artifact_links table for GOAP MinIO offload short-id mapping.

Revision ID: core_045
Revises: core_044
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa

revision = "core_045"
down_revision = "core_044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifact_links",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("short_id", sa.String(length=80), nullable=False),
        sa.Column("bucket", sa.String(length=128), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column(
            "content_type",
            sa.String(length=128),
            server_default="text/markdown",
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=64), server_default="blob", nullable=False),
        sa.Column("bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("short_id", name="uq_artifact_links_short_id"),
    )
    op.create_index("ix_artifact_links_short_id", "artifact_links", ["short_id"])
    op.create_index("ix_artifact_links_tenant_id", "artifact_links", ["tenant_id"])
    op.create_index("ix_artifact_links_session_id", "artifact_links", ["session_id"])
    op.create_index(
        "ix_artifact_links_tenant_session",
        "artifact_links",
        ["tenant_id", "session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_links_tenant_session", table_name="artifact_links")
    op.drop_index("ix_artifact_links_session_id", table_name="artifact_links")
    op.drop_index("ix_artifact_links_tenant_id", table_name="artifact_links")
    op.drop_index("ix_artifact_links_short_id", table_name="artifact_links")
    op.drop_table("artifact_links")
