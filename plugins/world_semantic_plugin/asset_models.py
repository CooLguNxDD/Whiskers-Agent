"""Asset desired-state and vectors; base table DDL owned by Alembic core_051, v3 additions by plugin 0010."""
from sqlalchemy import (BigInteger, CheckConstraint, Column, ForeignKeyConstraint, Integer, String, Text,
                        TIMESTAMP, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector
from db_layer.models.base import Base


class WorldAssetEmbedding(Base):
    """One current asset per authorized tenant/world namespace, never a global library."""

    __tablename__ = "world_asset_embeddings"
    __table_args__ = (
        CheckConstraint("tenant_id > 0", name="world_asset_tenant_positive"),
        CheckConstraint("dimensions > 0", name="world_asset_dimensions_positive"),
        CheckConstraint("kind IN ('prop','block','texture','audio','stamp')", name="world_asset_kind"),
        CheckConstraint("status IN ('enqueued','indexed','failed')", name="world_asset_status"),
        CheckConstraint("embedding IS NULL OR vector_dims(embedding) = dimensions", name="world_asset_vector_width"),
    )
    tenant_id = Column(BigInteger, primary_key=True)
    world_id = Column(String(128), primary_key=True)
    asset_id = Column(String(128), primary_key=True)
    kind = Column(String, nullable=False)
    content_text = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    generation = Column(String(32), nullable=False)
    model = Column(String, nullable=False)
    dimensions = Column(Integer, nullable=False)
    meta = Column(JSONB, nullable=False)
    embedding = Column(Vector(), nullable=True)
    status = Column(String, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    embedded_at = Column(TIMESTAMP(timezone=True), nullable=True)
    # WorldGen v3 pack columns (plugin migration 0010_asset_v3_library); v1 rows keep the defaults.
    pack_id = Column(String(128), nullable=True)
    distribution = Column(String(16), nullable=False, server_default=text("'shared'"))
    tri_count = Column(BigInteger, nullable=True)
    role = Column(String(128), nullable=True)
    biome = Column(String(128), nullable=True)
    licence = Column(String(128), nullable=True)


class WorldAssetVector(Base):
    """One current image-view or caption vector of a v3 asset (several rows per asset)."""

    __tablename__ = "world_asset_vectors"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "world_id", "asset_id"],
                             ["world_asset_embeddings.tenant_id", "world_asset_embeddings.world_id",
                              "world_asset_embeddings.asset_id"], ondelete="CASCADE"),
        CheckConstraint("vector_kind IN ('view','caption')", name="world_asset_vector_kind"),
        CheckConstraint("vector_dims(embedding) = dimensions", name="world_asset_vectors_width"),
    )
    tenant_id = Column(BigInteger, primary_key=True)
    world_id = Column(String(128), primary_key=True)
    asset_id = Column(String(128), primary_key=True)
    vector_kind = Column(String(16), primary_key=True)
    view_idx = Column(Integer, primary_key=True)
    model = Column(String, nullable=False)
    dimensions = Column(Integer, nullable=False)
    generation = Column(String(32), nullable=False)
    embedding = Column(Vector(), nullable=False)
