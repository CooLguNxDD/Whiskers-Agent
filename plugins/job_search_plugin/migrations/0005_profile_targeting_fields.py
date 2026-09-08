"""Job search plugin — structured targeting/skills columns on job_applicant_profiles.

Backs the compact/skills_only tiers of get_applicant_profile_summary (Stage 1 of the
Career-Ops integration plan): deterministic columns rather than deriving these fields
from preferences_text at read time, which would make the "compact" tier
non-deterministic and impossible to unit test reliably. Idempotent.
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Add compact/skills_only tier columns to job_applicant_profiles if missing."""
    conn.execute(text(
        "ALTER TABLE job_applicant_profiles ADD COLUMN IF NOT EXISTS location VARCHAR"
    ))
    conn.execute(text(
        "ALTER TABLE job_applicant_profiles ADD COLUMN IF NOT EXISTS target_titles JSONB DEFAULT '[]'"
    ))
    conn.execute(text(
        "ALTER TABLE job_applicant_profiles ADD COLUMN IF NOT EXISTS top_skills JSONB DEFAULT '[]'"
    ))
    conn.execute(text(
        "ALTER TABLE job_applicant_profiles ADD COLUMN IF NOT EXISTS constraints_text TEXT"
    ))
    conn.execute(text(
        "ALTER TABLE job_applicant_profiles ADD COLUMN IF NOT EXISTS notice_period_days INTEGER"
    ))
    conn.execute(text(
        "ALTER TABLE job_applicant_profiles ADD COLUMN IF NOT EXISTS core_technologies JSONB DEFAULT '[]'"
    ))
    conn.execute(text(
        "ALTER TABLE job_applicant_profiles ADD COLUMN IF NOT EXISTS methodologies JSONB DEFAULT '[]'"
    ))
    conn.execute(text(
        "ALTER TABLE job_applicant_profiles ADD COLUMN IF NOT EXISTS languages JSONB DEFAULT '[]'"
    ))
