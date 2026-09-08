"""Job search plugin — profiles, applications, preference embeddings.

Idempotent DDL throughout. Uses EMBED_DIMENSIONS for VECTOR width.
"""

from __future__ import annotations

import os

from sqlalchemy import text


def upgrade(conn) -> None:
    """Create job_* tables and tenant seam if missing."""
    dim = os.environ.get("EMBED_DIMENSIONS", "1536")

    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS job_applicant_profiles (
                id BIGSERIAL PRIMARY KEY,
                full_name VARCHAR NOT NULL,
                email VARCHAR NOT NULL,
                phone VARCHAR,
                base_resume_text TEXT,
                cover_letter_template TEXT,
                resume_object_key VARCHAR,
                preferences_text TEXT,
                created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS job_applications (
                id BIGSERIAL PRIMARY KEY,
                applicant_profile_id BIGINT NOT NULL
                    REFERENCES job_applicant_profiles(id) ON DELETE CASCADE,
                job_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                provider_application_id VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'drafted',
                notes TEXT,
                resume_object_key VARCHAR,
                cover_letter_object_key VARCHAR,
                created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_job_applications_profile_id
                ON job_applications(applicant_profile_id)
            """
        )
    )
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS job_preference_embeddings (
                id BIGSERIAL PRIMARY KEY,
                applicant_profile_id BIGINT NOT NULL
                    REFERENCES job_applicant_profiles(id) ON DELETE CASCADE,
                source VARCHAR NOT NULL,
                content TEXT NOT NULL,
                embedding VECTOR({dim}),
                model VARCHAR NOT NULL,
                meta JSONB DEFAULT '{{}}' NOT NULL,
                content_hash VARCHAR NOT NULL,
                synced_at TIMESTAMPTZ DEFAULT now() NOT NULL,
                CONSTRAINT job_pref_embeddings_profile_source_hash_key
                    UNIQUE (applicant_profile_id, source, content_hash)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_job_preference_embeddings_profile_id
                ON job_preference_embeddings(applicant_profile_id)
            """
        )
    )

    for table in (
        "job_applicant_profiles",
        "job_applications",
        "job_preference_embeddings",
    ):
        conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id BIGINT")
        )
        conn.execute(
            text(f"UPDATE {table} SET tenant_id = 1 WHERE tenant_id IS NULL")
        )
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant_id "
                f"ON {table} (tenant_id)"
            )
        )
