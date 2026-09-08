from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    TIMESTAMP,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from db_layer.models.base import Base


class WorkflowPlan(Base):
    """Persisted YAML workflow plan — audit log + reuse store (core_020).

    Each dynamic-graph run stores the builder-generated YAML, the compiled
    ExecutionStep[] plan, declared outputs, and the model metadata used.
    """

    __tablename__ = "workflow_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    session_id = Column(Text, nullable=True, index=True)
    name = Column(Text, nullable=False)
    user_query = Column(Text, nullable=False)
    yaml_content = Column(Text, nullable=False)
    compiled_plan = Column(JSONB, nullable=False, server_default="[]")
    outputs = Column(JSONB, nullable=False, server_default="{}")
    llm_provider = Column(String(32), nullable=False)
    llm_model = Column(String(128), nullable=False)
    embed_model = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, server_default="generated")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    last_executed_at = Column(TIMESTAMP(timezone=True), nullable=True)


class WorkflowExecution(Base):
    """Execution step record for a specific workflow plan run / goal-loop iteration (core_025)."""

    __tablename__ = "workflow_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    session_id = Column(Text, nullable=True, index=True)
    workflow_plan_id = Column(UUID(as_uuid=True), ForeignKey("workflow_plans.id", ondelete="SET NULL"), nullable=True)
    user_query = Column(Text, nullable=False)
    iteration = Column(Integer, nullable=False, server_default="0")
    step_results = Column(JSONB, nullable=False, server_default="[]")
    working_memory = Column(JSONB, nullable=False, server_default="{}")
    summary = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    carry = Column(JSONB, nullable=False, server_default="{}")
    status = Column(String(32), nullable=False, server_default="running")
    input_tokens = Column(Integer, nullable=False, server_default="0")
    output_tokens = Column(Integer, nullable=False, server_default="0")
    total_tokens = Column(Integer, nullable=False, server_default="0")
    model_usage = Column(JSONB, nullable=False, server_default="{}")
    tenant_id = Column(BigInteger, nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class Goal(Base):
    """Goal execution record for the achieve() pipeline (core_goal_001)."""
    __tablename__ = "goals"
    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    raw_goal = Column(Text, nullable=False)
    goal_spec = Column(JSONB, nullable=True)     # serialised GoalSpec
    status = Column(String(32), nullable=False, server_default="queued")  # queued|running|done|failed
    result = Column(JSONB, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class ChatSession(Base):
    """Playground chat session — groups a thread of messages (core_chat_001)."""
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    title = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class ChatMessage(Base):
    """A single message in a playground chat session (core_chat_001)."""
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(16), nullable=False)   # 'user' | 'assistant'
    content = Column(Text, nullable=False)
    engine = Column(String(32), nullable=True)   # 'agent' | None
    goap_state = Column(JSONB, nullable=True)
    raw = Column(JSONB, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
