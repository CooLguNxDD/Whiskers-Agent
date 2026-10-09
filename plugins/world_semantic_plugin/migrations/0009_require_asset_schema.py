"""Lifecycle guard only: core_051 exclusively owns asset table DDL."""
from sqlalchemy import text


def upgrade(conn):
    """Fail plugin loading closed until the approved Alembic asset revision is applied."""
    exists = conn.execute(text("SELECT to_regclass('world_asset_embeddings')")).scalar()
    if not exists:
        raise RuntimeError("world assets require Alembic core_051; run the core migration first")
