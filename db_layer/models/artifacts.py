"""Artifact link model — short-id → MinIO object mapping for GOAP offload."""

from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    String,
    Text,
    TIMESTAMP,
    func,
)
from db_layer.models.base import Base


class ArtifactLink(Base):
    """Maps a short public-looking id to a tenant-scoped MinIO object.

    Used by GOAP artifact offload so large step_results fields are stored
    outside the run_graph envelope and retrieved via session-gated REST or
    MCP tools (never via AuthPolicy.PUBLIC).
    """

    __tablename__ = "artifact_links"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    short_id = Column(String(80), nullable=False, unique=True, index=True)
    bucket = Column(String(128), nullable=False)
    object_key = Column(Text, nullable=False)
    content_type = Column(String(128), nullable=False, server_default="text/markdown")
    kind = Column(String(64), nullable=False, server_default="blob")
    bytes = Column(Integer, nullable=False, server_default="0")
    source_path = Column(Text, nullable=True)
    tenant_id = Column(BigInteger, nullable=False, index=True)
    session_id = Column(Text, nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
