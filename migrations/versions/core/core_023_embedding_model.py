"""Pickable embedding models core migration.

Revision ID: core_023
Revises: core_022
Create Date: 2026-06-04
"""

import os
from alembic import op
import sqlalchemy as sa

revision = "core_023"
down_revision = "core_022"
branch_labels = None
depends_on = None


def get_default_model_id() -> str:
    provider = os.environ.get("EMBED_PROVIDER", os.environ.get("LLM_PROVIDER", "openai")).lower()
    fallback_models = {
        "openai": "text-embedding-3-small",
        "gemini": "gemini-embedding-001",
        "anthropic": "text-embedding-3-small",
    }
    model_name = os.environ.get("EMBED_MODEL", "") or fallback_models.get(provider, "text-embedding-3-small")
    dims = int(os.environ.get("EMBED_DIMENSIONS", "") or 1536)
    return f"{provider}:{model_name}:{dims}"


def upgrade() -> None:
    # 1. Drop HNSW index from route_embeddings (HNSW requires fixed dimension)
    op.execute("DROP INDEX IF EXISTS route_embeddings_hnsw")

    # 2. Alter embedding column on route_embeddings to dimensionless vector
    op.execute("ALTER TABLE route_embeddings ALTER COLUMN embedding TYPE vector USING embedding::vector")

    # 3. Add model column to route_embeddings
    op.execute("ALTER TABLE route_embeddings ADD COLUMN IF NOT EXISTS model VARCHAR")

    # 4. Backfill model column with default_model_id
    default_model_id = get_default_model_id()
    op.get_bind().execute(
        sa.text("UPDATE route_embeddings SET model = :model_id WHERE model IS NULL"),
        {"model_id": default_model_id}
    )
    op.execute("ALTER TABLE route_embeddings ALTER COLUMN model SET NOT NULL")

    # 5. Swap unique constraints on route_embeddings to include model
    op.execute("ALTER TABLE route_embeddings DROP CONSTRAINT IF EXISTS route_embeddings_plugin_op_unique")
    op.execute(
        "ALTER TABLE route_embeddings ADD CONSTRAINT route_embeddings_plugin_op_unique UNIQUE (plugin_id, operation_id, model)"
    )

    # 6. Add embedding_model column to tool_config
    op.execute("ALTER TABLE tool_config ADD COLUMN IF NOT EXISTS embedding_model VARCHAR NULL")


def downgrade() -> None:
    dim = os.environ.get("EMBED_DIMENSIONS", "1536")
    default_model_id = get_default_model_id()

    # 1. Drop embedding_model from tool_config
    op.execute("ALTER TABLE tool_config DROP COLUMN IF EXISTS embedding_model")

    # Check if route_embeddings table exists before modifying it
    conn = op.get_bind()
    table_exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'route_embeddings')"
    )).scalar()

    if table_exists:
        # 2. Keep only rows matching the default model to avoid constraint violation on downgrade
        op.get_bind().execute(
            sa.text("DELETE FROM route_embeddings WHERE model IS NOT NULL AND model != :model_id"),
            {"model_id": default_model_id}
        )

        # 3. Swap unique constraints on route_embeddings back to (plugin_id, operation_id)
        op.execute("ALTER TABLE route_embeddings DROP CONSTRAINT IF EXISTS route_embeddings_plugin_op_unique")
        op.execute(
            "ALTER TABLE route_embeddings ADD CONSTRAINT route_embeddings_plugin_op_unique UNIQUE (plugin_id, operation_id)"
        )

        # 4. Drop model column from route_embeddings
        op.execute("ALTER TABLE route_embeddings DROP COLUMN IF EXISTS model")

        # 5. Alter embedding column back to fixed-dimension vector
        op.execute(f"ALTER TABLE route_embeddings ALTER COLUMN embedding TYPE vector({dim}) USING embedding::vector({dim})")

        # 6. Recreate HNSW index
        op.execute("CREATE INDEX IF NOT EXISTS route_embeddings_hnsw ON route_embeddings USING hnsw (embedding vector_cosine_ops)")
