"""Add y_layer to objects and layer_height to worlds (3D hex prisms)."""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    # Per-world vertical slab height (meters). Changing requires reindex.
    conn.execute(
        text(
            """
            ALTER TABLE worlds
            ADD COLUMN IF NOT EXISTS layer_height DOUBLE PRECISION NOT NULL DEFAULT 32.0
            """
        )
    )

    # Discrete vertical layer for each object.
    conn.execute(
        text(
            """
            ALTER TABLE objects
            ADD COLUMN IF NOT EXISTS y_layer INT NOT NULL DEFAULT 0
            """
        )
    )

    # Backfill from pos[2] is Y in world space? Schema: pos REAL[3] = [x,y,z] → index 2 is Y? 
    # Unity sends [x,y,z] so PostgreSQL arrays are 1-indexed: pos[1]=x, pos[2]=y, pos[3]=z.
    conn.execute(
        text(
            """
            UPDATE objects
            SET y_layer = FLOOR(COALESCE(pos[2], 0.0) / 32.0)::INT
            WHERE pos IS NOT NULL
            """
        )
    )

    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_objects_world_hex_ylayer
            ON objects (world_id, hex_id, y_layer)
            """
        )
    )
