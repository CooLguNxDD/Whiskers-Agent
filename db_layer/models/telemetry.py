from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    Integer,
    String,
    TIMESTAMP,
    text,
)
from db_layer.models.base import Base


class ToolCallEvent(Base):
    """Record of an MCP tool call (for analytics)."""
    __tablename__ = "tool_call_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tool_name = Column(String(256), nullable=False, index=True)
    plugin_id = Column(String(256), nullable=False)
    model = Column(String(128), nullable=True)
    latency_ms = Column(Integer, nullable=False)
    ok = Column(Boolean, nullable=False)
    error_type = Column(String(128), nullable=True)
    status_code = Column(Integer, nullable=True)
    subject = Column(String(256), nullable=True)
    tenant_id = Column(BigInteger, nullable=True, index=True)
    feature = Column(String(16), nullable=False, server_default=text("'mcp'"))
    parent_run_id = Column(String(64), nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"), index=True)


class GraphRunEvent(Base):
    """Record of one LangGraph agent run (feature='graph' KPI counterpart to ToolCallEvent)."""
    __tablename__ = "graph_run_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(BigInteger, nullable=False, index=True)
    subject = Column(String(256), nullable=True)
    mode = Column(String(32), nullable=True)
    flow_id = Column(String(128), nullable=True)
    goal_class = Column(String(64), nullable=True)
    ok = Column(Boolean, nullable=False)
    error_type = Column(String(128), nullable=True)
    terminal_status = Column(String(32), nullable=True)
    latency_ms = Column(Integer, nullable=False)
    step_count = Column(Integer, nullable=True)
    failed_steps = Column(Integer, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"), index=True)


class RelaySessionEvent(Base):
    """Record of a terminal relay session open/close or elevation event."""
    __tablename__ = "relay_session_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event = Column(String(32), nullable=False, index=True)  # open, close, elevate_ok, elevate_fail
    session_id = Column(String(128), nullable=False)
    subject = Column(String(256), nullable=False)
    ide_id = Column(String(128), nullable=True)
    bytes_total = Column(BigInteger, nullable=True)
    duration_s = Column(Float, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"), index=True)
