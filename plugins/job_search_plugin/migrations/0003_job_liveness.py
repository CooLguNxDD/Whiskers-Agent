"""Job search plugin — job_posting_liveness table (repost/ghost-job tracking). Idempotent."""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Create job_posting_liveness if missing."""
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS job_posting_liveness (
            id BIGSERIAL PRIMARY KEY,
            url_hash VARCHAR(64) UNIQUE NOT NULL,
            url TEXT NOT NULL,
            company VARCHAR NOT NULL,
            role_title VARCHAR NOT NULL,
            provider VARCHAR,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            is_active BOOLEAN NOT NULL DEFAULT true,
            repost_count INTEGER NOT NULL DEFAULT 1,
            staleness_days INTEGER NOT NULL DEFAULT 0,
            ghost_signals JSON DEFAULT '{}',
            tenant_id BIGINT
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_job_posting_liveness_company ON job_posting_liveness (company)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_job_posting_liveness_tenant_id ON job_posting_liveness (tenant_id)"
    ))
