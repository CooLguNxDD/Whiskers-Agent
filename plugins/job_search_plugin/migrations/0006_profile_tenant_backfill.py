"""Backfill legacy job-search profile and preference rows to tenant 1."""

from sqlalchemy import text


def upgrade(conn) -> None:
    """Assign legacy NULL tenant ids to the original default tenant."""
    conn.execute(text(
        "UPDATE job_applicant_profiles SET tenant_id = 1 WHERE tenant_id IS NULL"
    ))
    conn.execute(text(
        "UPDATE job_preference_embeddings SET tenant_id = 1 WHERE tenant_id IS NULL"
    ))
