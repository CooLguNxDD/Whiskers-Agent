"""World asset vectors (approved Alembic exception; no plugin-local DDL).

Revision ID: core_051
Revises: core_050
The plugin retains ORM/store ownership. Its lifecycle step only checks existence.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

revision = "core_051"
down_revision = "core_050"
branch_labels = None
depends_on = None


def upgrade():
    """Create the namespace-keyed desired state and exact-width current vector table."""
    op.create_table(
        "world_asset_embeddings",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("world_id", sa.String(128), nullable=False),
        sa.Column("asset_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("generation", sa.String(32), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("meta", JSONB(), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("embedded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "world_id", "asset_id", name="world_asset_identity"),
        sa.CheckConstraint("tenant_id > 0", name="world_asset_tenant_positive"),
        sa.CheckConstraint("dimensions > 0", name="world_asset_dimensions_positive"),
        sa.CheckConstraint("kind IN ('prop','block','texture','audio','stamp')", name="world_asset_kind"),
        sa.CheckConstraint("status IN ('enqueued','indexed','failed')", name="world_asset_status"),
        sa.CheckConstraint("embedding IS NULL OR vector_dims(embedding) = dimensions", name="world_asset_vector_width"),
    )
    # Unconstrained Vector permits configured widths; dense search filters model before top-k.
    op.create_index("world_asset_search_namespace", "world_asset_embeddings", ["tenant_id", "world_id", "model", "kind"])


def downgrade():
    """Remove only the asset table owned by this revision."""
    op.drop_index("world_asset_search_namespace", table_name="world_asset_embeddings")
    op.drop_table("world_asset_embeddings")
