# ---------------------------------------------------------------------------
# Embedding + cache models (unchanged)
# ---------------------------------------------------------------------------
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Computed,
    Integer,
    JSON,
    String,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from pgvector.sqlalchemy import Vector
from db_layer.models.base import Base

# Postgres GENERATED ALWAYS ... STORED expressions (core_043 / core_044 /
# world_semantic 0007). Must use sqlalchemy.Computed so ORM INSERT/UPDATE
# omit search_doc — a plain Column(TSVECTOR) sends search_doc=NULL and Postgres
# raises GeneratedAlways. Fresh Computed() per column (do not share instances).


class SearchContentVector(Base):
    """Vector embeddings for the search engine (semantic_index / semantic_search)."""

    __tablename__ = "search_content_vectors"
    __table_args__ = (
        UniqueConstraint(
            "collection",
            "content_hash",
            "model",
            name="search_content_vectors_collection_hash_model_key",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    collection = Column(String, nullable=False)
    content_text = Column(Text, nullable=False)
    embedding = Column(Vector(), nullable=True)
    model = Column(String, nullable=False)
    meta = Column(JSON, server_default="{}")
    content_hash = Column(String, nullable=False)
    tenant_id = Column(BigInteger, nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default="now()")
    search_doc = Column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(content_text, ''))", persisted=True),
    )


class MemoryContentVector(Base):
    """Vector embeddings for core memory / harness RAG (agent notes, recipes)."""

    __tablename__ = "memory_content_vectors"
    __table_args__ = (
        UniqueConstraint(
            "collection",
            "content_hash",
            "model",
            name="memory_content_vectors_collection_hash_model_key",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    collection = Column(String, nullable=False)
    content_text = Column(Text, nullable=False)
    embedding = Column(Vector(), nullable=True)
    model = Column(String, nullable=False)
    meta = Column(JSON, server_default="{}")
    content_hash = Column(String, nullable=False)
    tenant_id = Column(BigInteger, nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default="now()")
    search_doc = Column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(content_text, ''))", persisted=True),
    )

# Back-compat alias (search backend). Prefer SearchContentVector in new code.
ContentVector = SearchContentVector


class RouteEmbedding(Base):
    """Vector embeddings for API route descriptions (semantic route matching).

    Lifted to core in core_014. The dynamic graph reads this table to pick
    candidate operations for a user query; embeddings are produced by the
    async worker (see ``core_graph.worker.embedding_worker``).
    """

    __tablename__ = "route_embeddings"
    __table_args__ = (
        UniqueConstraint("plugin_id", "operation_id", "model", name="route_embeddings_plugin_op_unique"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    plugin_id = Column(String, nullable=False, index=True)
    operation_id = Column(String, nullable=False)
    method = Column(String(10), nullable=False)
    # Legacy column kept for backward compatibility with pro_001 data.
    path = Column(Text, nullable=False)
    path_template = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    parameters = Column(JSONB, server_default="{}")
    is_fast_path = Column(Boolean, nullable=False, server_default="false")
    is_enabled = Column(Boolean, nullable=False, server_default="true")
    content_hash = Column(String, nullable=False)
    meta = Column(JSON, server_default="{}")
    embedding = Column(Vector(), nullable=True)
    model = Column(String, nullable=False)
    doc_text = Column(Text, nullable=True)
    search_doc = Column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(doc_text, ''))", persisted=True),
    )
    embedded_at = Column(TIMESTAMP(timezone=True), nullable=True)
    synced_at = Column(TIMESTAMP(timezone=True), server_default="now()")


class EmbeddingJob(Base):
    """Async work queue for route-embedding generation (created in core_014).

    Producer (``core_graph.worker.job_producer``) inserts pending rows and
    fires ``NOTIFY embedding_jobs_new``; the worker
    (``core_graph.worker.embedding_worker``) claims batches via
    ``FOR UPDATE SKIP LOCKED`` and marks them done/failed atomically.
    """

    __tablename__ = "embedding_jobs"
    __table_args__ = (
        UniqueConstraint("plugin_id", "operation_id", "content_hash",
                         name="embedding_jobs_dedup"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    plugin_id = Column(String, nullable=False)
    operation_id = Column(String, nullable=False)
    content_hash = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    status = Column(String, nullable=False, server_default="pending")
    attempts = Column(Integer, nullable=False, server_default="0")
    last_error = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    claimed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    completed_at = Column(TIMESTAMP(timezone=True), nullable=True)
