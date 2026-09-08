"""world_semantic_plugin — durable queue for world index batches.

POST /api/world/{id}/index enqueues rows here; world_index_worker claims them
via FOR UPDATE SKIP LOCKED (same pattern as embedding_jobs).
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS world_index_jobs (
                id              BIGSERIAL PRIMARY KEY,
                world_id        TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                replace         BOOLEAN NOT NULL DEFAULT FALSE,
                world_meta      JSONB NOT NULL DEFAULT '{}'::jsonb,
                objects         JSONB NOT NULL DEFAULT '[]'::jsonb,
                objects_count   INT NOT NULL DEFAULT 0,
                batch_index     INT,
                batch_total     INT,
                client_run_id   TEXT,
                result          JSONB,
                last_error      TEXT,
                attempts        INT NOT NULL DEFAULT 0,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                claimed_at      TIMESTAMPTZ,
                completed_at    TIMESTAMPTZ
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_world_index_jobs_pending
            ON world_index_jobs (created_at)
            WHERE status = 'pending'
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_world_index_jobs_world
            ON world_index_jobs (world_id, created_at DESC)
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_world_index_jobs_run
            ON world_index_jobs (client_run_id)
            WHERE client_run_id IS NOT NULL
            """
        )
    )
