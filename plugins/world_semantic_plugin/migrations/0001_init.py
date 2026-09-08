"""world_semantic_plugin — spatial store tables (worlds / hexes / objects / edges / assets).

Follows the plugin migration style: callable upgrade(conn), idempotent DDL,
VECTOR width from EMBED_DIMENSIONS.
"""

from __future__ import annotations

import os

from sqlalchemy import text


def upgrade(conn) -> None:
    """Create world-semantic spatial tables if missing."""
    dim = os.environ.get("EMBED_DIMENSIONS", "1536")

    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS worlds (
                world_id            TEXT PRIMARY KEY,
                name                TEXT,
                anchor_lat          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                anchor_lng          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                meters_per_degree   DOUBLE PRECISION NOT NULL DEFAULT 111320.0,
                base_res            SMALLINT NOT NULL DEFAULT 9,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )

    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS hexes (
                world_id            TEXT NOT NULL REFERENCES worlds(world_id) ON DELETE CASCADE,
                hex_id              TEXT NOT NULL,
                res                 SMALLINT NOT NULL,
                parent_hex          TEXT,
                biome               TEXT,
                elevation_band      SMALLINT,
                tags                JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                object_count        INT NOT NULL DEFAULT 0,
                summary             TEXT,
                embedding           VECTOR({dim}),
                dirty               BOOLEAN NOT NULL DEFAULT TRUE,
                lease_holder        TEXT,
                lease_expiry        TIMESTAMPTZ,
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (world_id, hex_id)
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_hexes_world_parent "
            "ON hexes (world_id, parent_hex)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_hexes_world_dirty "
            "ON hexes (world_id, dirty) WHERE dirty = TRUE"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_hexes_world_lease "
            "ON hexes (world_id, lease_holder) WHERE lease_holder IS NOT NULL"
        )
    )

    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS objects (
                world_id            TEXT NOT NULL REFERENCES worlds(world_id) ON DELETE CASCADE,
                guid                UUID NOT NULL,
                hex_id              TEXT NOT NULL,
                name                TEXT,
                prefab_path         TEXT,
                components          JSONB NOT NULL DEFAULT '[]'::jsonb,
                state               JSONB NOT NULL DEFAULT '{}'::jsonb,
                pos                 REAL[3],
                bounds              REAL[6],
                tags                JSONB NOT NULL DEFAULT '[]'::jsonb,
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (world_id, guid)
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_objects_world_hex "
            "ON objects (world_id, hex_id)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_objects_world_name "
            "ON objects (world_id, name)"
        )
    )

    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS edges (
                world_id            TEXT NOT NULL REFERENCES worlds(world_id) ON DELETE CASCADE,
                src_hex             TEXT NOT NULL,
                dst_hex             TEXT NOT NULL,
                kind                TEXT NOT NULL,
                weight              REAL NOT NULL DEFAULT 1.0,
                PRIMARY KEY (world_id, src_hex, dst_hex, kind)
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_edges_world_src "
            "ON edges (world_id, src_hex)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_edges_world_dst "
            "ON edges (world_id, dst_hex)"
        )
    )

    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS assets (
                world_id            TEXT NOT NULL REFERENCES worlds(world_id) ON DELETE CASCADE,
                asset_path          TEXT NOT NULL,
                kind                TEXT,
                deps                JSONB NOT NULL DEFAULT '[]'::jsonb,
                instance_hexes      TEXT[] NOT NULL DEFAULT '{}',
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (world_id, asset_path)
            )
            """
        )
    )
