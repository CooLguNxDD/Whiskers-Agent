"""world_semantic_plugin — dedicated Unity world RAG vector table.

Isolated from content_vectors / route_embeddings. Filled by embedding_worker
via operation_id=upsert_unity_world_vector (not hexes.embedding).
"""

from __future__ import annotations

import os

from sqlalchemy import text


def upgrade(conn) -> None:
    # Must match global EMBED_DIMENSIONS (same as .env / .env_sample embed block
    # and route/contact vector tables). Do not hardcode a plugin-local size.
    dim = int(os.environ.get("EMBED_DIMENSIONS", "") or 1536)
    if dim < 1:
        dim = 1536

    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS unity_world_vectors (
                id              BIGSERIAL PRIMARY KEY,
                world_id        TEXT NOT NULL,
                doc_kind        TEXT NOT NULL,
                doc_id          TEXT NOT NULL,
                content_text    TEXT NOT NULL,
                content_hash    TEXT NOT NULL,
                embedding       VECTOR({dim}),
                model           TEXT NOT NULL,
                meta            JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                embedded_at     TIMESTAMPTZ,
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT unity_world_vectors_identity_unique
                    UNIQUE (world_id, doc_kind, doc_id, model)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_unity_world_vectors_world
            ON unity_world_vectors (world_id)
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_unity_world_vectors_world_kind
            ON unity_world_vectors (world_id, doc_kind)
            """
        )
    )
