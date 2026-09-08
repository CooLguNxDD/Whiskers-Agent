"""SQLAlchemy ORM model for ProxyServer."""

from sqlalchemy import Column, String, Text, Boolean, Integer, DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from db_layer.models import Base


class ProxyServer(Base):
    """ProxyServer model representing registered upstream MCP servers."""

    __tablename__ = "proxy_servers"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name = Column(String, nullable=False, unique=True)
    transport = Column(String, nullable=False)  # http | sse
    url = Column(Text, nullable=False)
    status = Column(String, nullable=False, server_default="active")  # active | inactive | error
    has_auth = Column(Boolean, nullable=False, server_default="false")  # retained for legacy compat
    auth_mode = Column(String, nullable=False, server_default="none")  # none | bearer | oauth
    oauth_config = Column(JSONB, nullable=True)  # non-secret OAuth provider config
    tool_count = Column(Integer, nullable=False, server_default="0")
    error_message = Column(Text, nullable=True)
    custom_description = Column(Text, nullable=True)  # gateway-side context prepended to each proxy tool's description
    workspace_label = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
