"""Enable PostgreSQL extensions: pgcrypto, pg_trgm.

Revision ID: core_001
Revises: 
Create Date: 2026-04-14

Note: pg_cron must be installed at the OS/DB level (shared_preload_libraries).
      This migration only enables extensions available at CREATE EXTENSION time.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgcrypto: provides pgp_sym_encrypt / pgp_sym_decrypt for at-rest encryption
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    # pg_trgm: trigram indexes for ILIKE / similarity search
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # vector: pgvector for semantic search
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Only drop if nothing depends on these extensions — be careful in prod
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
