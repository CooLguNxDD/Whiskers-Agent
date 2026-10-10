"""Allow configured per-model widths without deleting or re-embedding legacy vectors.

The ORM already uses Vector() and identities include provider:model:dimensions.
Unbounded vector columns support exact-width filtered dense search; any future
ANN index must use a per-space partial expression index, not one global width.
"""
from sqlalchemy import text


def upgrade(conn) -> None:
    """Remove the old global width typmod while preserving every stored vector."""
    conn.execute(text("ALTER TABLE unity_world_vectors ALTER COLUMN embedding TYPE vector"))
