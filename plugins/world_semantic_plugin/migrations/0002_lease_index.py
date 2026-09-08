"""world_semantic_plugin — reinforce lease indexes (step 5).

Lease columns already exist from 0001_init; this step adds a partial composite
index for expiry sweeps if missing.
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_hexes_world_lease_expiry
            ON hexes (world_id, lease_expiry)
            WHERE lease_holder IS NOT NULL
            """
        )
    )
