from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    TIMESTAMP,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from db_layer.models.base import Base


class ToolConfig(Base):
    """Per-tool MCP-exposure toggle (core_017).

    Decides whether an individual ``@mcp.tool()`` is registered with FastMCP
    and therefore visible/callable by MCP clients. Independent of
    ``RouteEmbedding.is_enabled`` (which only gates semantic-search routing).
    Sparse — only disabled tools need a row; absence means enabled.
    """

    __tablename__ = "tool_config"

    plugin_id = Column(Text, primary_key=True)
    tool_name = Column(Text, primary_key=True)
    is_enabled = Column(Boolean, nullable=False, server_default="true")
    # Gateway (run_graph unified) hide flag. Hidden = invisible to MCP clients
    # but still reachable internally by run_graph. Distinct from is_enabled.
    is_hidden = Column(Boolean, nullable=False, server_default="false")
    embedding_model = Column(String, nullable=True)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class ToolPermission(Base):
    """Per-(plugin, operation) read/write policy for the permission gate (core_028).

    Sparse — absent row = default (allow_read=true, allow_write=true, require_confirmation=true).
    Policy is evaluated by operation class (read vs write derived from HTTP method).
    Enforced inside the LangGraph before builder execution.
    """

    __tablename__ = "tool_permissions"

    plugin_id = Column(Text, primary_key=True)
    operation_id = Column(Text, primary_key=True)
    allow_read = Column(Boolean, nullable=False, server_default="true")
    allow_write = Column(Boolean, nullable=False, server_default="false")
    require_confirmation = Column(Boolean, nullable=False, server_default="true")
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class ApiCache(Base):
    """Key/value cache for raw upstream API responses."""

    __tablename__ = "api_cache"

    cache_key = Column(String, primary_key=True)
    payload = Column(JSON, nullable=False)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)


class ServerSetting(Base):
    """Key/value store for runtime server config overrides (core_016).

    ``key`` is a namespace string (e.g. ``'llm'``).
    ``value`` is a JSONB blob of non-sensitive settings.
    API keys are NEVER stored here — they remain in environment variables.
    """

    __tablename__ = "server_settings"

    key = Column(Text, primary_key=True)
    value = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class LLMPoolEntry(Base):
    """A selectable LLM / embedding model in the dynamic provider pool (core_019).

    The graph + embeddings resolve their model from the active pool entry. The
    ``api_key`` BYTEA column holds pgp_sym_encrypt(token, master_key) — encrypted
    at rest with the same pgcrypto key as the vault; never read it directly,
    always project through ``pgp_sym_decrypt(...)`` (see core/llm_config_service).
    """

    __tablename__ = "llm_pool"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name = Column(Text, nullable=False, unique=True)
    kind = Column(Text, nullable=False, server_default="chat")   # 'chat' | 'embedding'
    provider = Column(Text, nullable=False)                       # openai | anthropic | gemini
    model = Column(Text, nullable=False)
    dimensions = Column(Integer, nullable=True)                   # embedding only
    base_url = Column(Text, nullable=True)
    strength = Column(Float, nullable=False, server_default="1.0")
    # pgp_sym_encrypt(api_token, master_key) → BYTEA (NULL when env key is used)
    api_key = Column(LargeBinary, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
