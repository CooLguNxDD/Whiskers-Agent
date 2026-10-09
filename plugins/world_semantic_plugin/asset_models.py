"""Asset desired-state and vectors; DDL owned only by Alembic core_051."""
from sqlalchemy import BigInteger, CheckConstraint, Column, Integer, String, Text, TIMESTAMP, func
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
