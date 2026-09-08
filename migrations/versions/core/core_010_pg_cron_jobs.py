"""Install pg_cron jobs for automated cleanup of expired rows.

Revision ID: core_010
Revises: core_009
Create Date: 2026-04-14

Requires pg_cron to be loaded in shared_preload_libraries.
If pg_cron is not available this migration degrades gracefully with a WARNING
and the cleanup must be handled externally.

Scheduled jobs:
  - Every 5 minutes: delete expired / used oauth_auth_codes
  - Every 15 minutes: delete expired plugin_oauth_pkce_state rows
  - Every hour: delete expired / revoked oauth_tokens
"""

from alembic import op

revision = "core_010"
down_revision = "core_009"
branch_labels = None
depends_on = None


_CRON_JOBS = [
    (
        "mcp_cleanup_auth_codes",
        "*/5 * * * *",
        "DELETE FROM oauth_auth_codes WHERE expires_at < now() OR used_at IS NOT NULL",
    ),
    (
        "mcp_cleanup_pkce_state",
        "*/15 * * * *",
        "DELETE FROM plugin_oauth_pkce_state WHERE expires_at < now()",
    ),
    (
        "mcp_cleanup_oauth_tokens",
        "0 * * * *",
        "DELETE FROM oauth_tokens WHERE expires_at < now() OR revoked_at IS NOT NULL",
    ),
]


def upgrade() -> None:
    # Check whether pg_cron is available before scheduling
    check_sql = """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
            -- Schedule cleanup jobs
            PERFORM cron.schedule('mcp_cleanup_auth_codes',   '*/5 * * * *',
                'DELETE FROM oauth_auth_codes WHERE expires_at < now() OR used_at IS NOT NULL');
            PERFORM cron.schedule('mcp_cleanup_pkce_state',   '*/15 * * * *',
                'DELETE FROM plugin_oauth_pkce_state WHERE expires_at < now()');
            PERFORM cron.schedule('mcp_cleanup_oauth_tokens', '0 * * * *',
                'DELETE FROM oauth_tokens WHERE expires_at < now() OR revoked_at IS NOT NULL');
            RAISE NOTICE 'pg_cron jobs scheduled.';
        ELSE
            RAISE WARNING 'pg_cron extension not found. Expired token cleanup must be handled externally.';
        END IF;
    END
    $$;
    """
    op.execute(check_sql)


def downgrade() -> None:
    unschedule_sql = """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
            PERFORM cron.unschedule('mcp_cleanup_auth_codes');
            PERFORM cron.unschedule('mcp_cleanup_pkce_state');
            PERFORM cron.unschedule('mcp_cleanup_oauth_tokens');
        END IF;
    END
    $$;
    """
    op.execute(unschedule_sql)
