"""WorldGen v3 asset library: pack columns on the asset row + per-view vector table.

Idempotent (IF NOT EXISTS). The base table stays owned by Alembic core_051; this revision
only adds nullable/defaulted columns to it and creates the plugin-owned `world_asset_vectors`.
"""
from sqlalchemy import text


def upgrade(conn) -> None:
    """Add v3 filter columns and the max-sim vector table; v1 rows keep distribution='shared'."""
    if not conn.execute(text("SELECT to_regclass('world_asset_embeddings')")).scalar():
        raise RuntimeError("world assets require Alembic core_051; run the core migration first")
    for column in ("pack_id VARCHAR(128)", "distribution VARCHAR(16) NOT NULL DEFAULT 'shared'",
                   "tri_count BIGINT", "role VARCHAR(128)", "biome VARCHAR(128)", "licence VARCHAR(128)"):
        conn.execute(text(f"ALTER TABLE world_asset_embeddings ADD COLUMN IF NOT EXISTS {column}"))
    conn.execute(text("""
        DO $$ BEGIN
            ALTER TABLE world_asset_embeddings ADD CONSTRAINT world_asset_distribution
                CHECK (distribution IN ('shared','local'));
        EXCEPTION WHEN duplicate_object THEN NULL; END $$"""))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS world_asset_filter_idx ON world_asset_embeddings "
        "(tenant_id, world_id, distribution, pack_id, role, biome, licence)"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS world_asset_vectors (
            tenant_id BIGINT NOT NULL,
            world_id VARCHAR(128) NOT NULL,
            asset_id VARCHAR(128) NOT NULL,
            vector_kind VARCHAR(16) NOT NULL,
            view_idx INTEGER NOT NULL,
            model VARCHAR NOT NULL,
            dimensions INTEGER NOT NULL,
            generation VARCHAR(32) NOT NULL,
            embedding VECTOR NOT NULL,
            PRIMARY KEY (tenant_id, world_id, asset_id, vector_kind, view_idx),
            FOREIGN KEY (tenant_id, world_id, asset_id)
                REFERENCES world_asset_embeddings (tenant_id, world_id, asset_id) ON DELETE CASCADE,
            CONSTRAINT world_asset_vector_kind CHECK (vector_kind IN ('view','caption')),
            CONSTRAINT world_asset_vectors_width CHECK (vector_dims(embedding) = dimensions)
        )"""))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS world_asset_vectors_space_idx ON world_asset_vectors "
        "(tenant_id, world_id, model)"))
