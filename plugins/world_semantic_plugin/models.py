from sqlalchemy import (
    BigInteger,
    Column,
    Computed,
    String,
    Text,
    TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from pgvector.sqlalchemy import Vector
from db_layer.models.base import Base


class UnityWorldVector(Base):
    """Dedicated Unity world RAG vectors (object + hex docs).

    Schema owned by world_semantic_plugin migration 0004. Filled by
    ``embedding_worker`` for operation_id ``upsert_unity_world_vector``.
    Isolated from ``content_vectors`` and ``route_embeddings``.
    """

    __tablename__ = "unity_world_vectors"
    __table_args__ = (
        UniqueConstraint(
            "world_id",
            "doc_kind",
            "doc_id",
            "model",
            name="unity_world_vectors_identity_unique",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    world_id = Column(String, nullable=False, index=True)
    doc_kind = Column(String, nullable=False)  # object | hex
    doc_id = Column(String, nullable=False)
    content_text = Column(Text, nullable=False)
    content_hash = Column(String, nullable=False)
    embedding = Column(Vector(), nullable=True)
    model = Column(String, nullable=False)
    meta = Column(JSONB, server_default="{}")
    embedded_at = Column(TIMESTAMP(timezone=True), nullable=True)
    updated_at = Column(TIMESTAMP(timezone=True), server_default="now()")
    search_doc = Column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(content_text, ''))", persisted=True),
    )
