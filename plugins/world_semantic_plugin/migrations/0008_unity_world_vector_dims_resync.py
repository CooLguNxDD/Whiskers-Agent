"""Re-type unity_world_vectors.embedding to the live embedding width, if it drifted.

0005 already did this once, but plugin migrations are checksum-gated: a later
width change does not re-run 0005. This step compares the declared column
width with ``embedding_dimensions()`` and is a no-op when they match, so stored
vectors survive a normal upgrade.

On a mismatch the old rows are unusable (wrong width), so they are dropped and
every completed world embedding job is reset to ``pending``. Each job payload
carries its full ``content_text``, so the embedding worker rebuilds the vectors
without a Unity re-index.
"""

from __future__ import annotations

from sqlalchemy import text

# Mirrors unity_world_vectors_store.PLUGIN_ID / OPERATION_ID; importing the store
# here would pull the async DB session layer into the migration runner.
PLUGIN_ID = "world_semantic_plugin"
OPERATION_ID = "upsert_unity_world_vector"


def upgrade(conn) -> None:
    from core.embedding_dimensions import embedding_dimensions, vector_column_width

    dim = embedding_dimensions()
    current = vector_column_width(conn, "unity_world_vectors")
    if current is None or current == dim:
        return

    # Drop HNSW/IVF if any future index depends on fixed dim.
    conn.execute(text("DROP INDEX IF EXISTS unity_world_vectors_embedding_hnsw"))
    conn.execute(text("DROP INDEX IF EXISTS ix_unity_world_vectors_embedding"))
    conn.execute(text("DELETE FROM unity_world_vectors"))
    conn.execute(
        text(
            f"""
            ALTER TABLE unity_world_vectors
            ALTER COLUMN embedding TYPE vector({dim})
            USING NULL
            """
        )
    )
    # Same reset embed_job_store._insert_jobs applies on conflict.
    conn.execute(
        text(
            """
            UPDATE embedding_jobs
            SET status = 'pending', attempts = 0, last_error = NULL,
                claimed_at = NULL, completed_at = NULL
            WHERE plugin_id = :plugin_id AND operation_id = :operation_id
            """
        ),
        {"plugin_id": PLUGIN_ID, "operation_id": OPERATION_ID},
    )
    conn.execute(text("NOTIFY embedding_jobs_new"))
