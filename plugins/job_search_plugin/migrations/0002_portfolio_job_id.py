"""Job search plugin — portfolio_job_id trace column on job_applications.

Soft link (not FK) back to portfolio_job_layouts.short_id — the "bake & send"
baked-portfolio job id threaded through the apply pipeline. Idempotent.
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Add job_applications.portfolio_job_id if missing."""
    conn.execute(
        text("ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS portfolio_job_id VARCHAR")
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_job_applications_portfolio_job_id "
            "ON job_applications (portfolio_job_id)"
        )
    )
