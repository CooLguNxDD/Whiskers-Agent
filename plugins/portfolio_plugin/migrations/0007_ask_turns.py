"""Portfolio plugin — fish-tank visitor ask turn observability.

portfolio_ask_turns is the ask-mode counterpart to portfolio_bake_runs
(0005_bake_observability): a durable per-turn audit row (subject/session,
question, intent, outcome, latency) so "who asked what on the fish tank" is
a SELECT instead of invisible. Ask overlays themselves stay ephemeral —
this table never stores overlay blocks or the generated answer text.

Plugin: portfolio_plugin
Revision: 0007_ask_turns

Applied by PluginSchemaMigrator — receives a live SQLAlchemy connection.
See: db_layer/plugin_schema_migrator.py
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Create the portfolio_ask_turns table."""
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS portfolio_ask_turns (
                id BIGSERIAL PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE,
                tenant_id BIGINT NOT NULL,
                subject TEXT,
                visitor_session_id TEXT,
                question TEXT NOT NULL,
                intent TEXT,
                view TEXT,
                focus_slug TEXT,
                highlight_slugs JSONB,
                add_slugs JSONB,
                ok BOOLEAN NOT NULL DEFAULT true,
                error_type TEXT,
                latency_ms INTEGER,
                parent_run_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_ask_turns_tenant_created "
            "ON portfolio_ask_turns (tenant_id, created_at DESC)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_ask_turns_visitor_session_id "
            "ON portfolio_ask_turns (visitor_session_id)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_portfolio_ask_turns_intent "
            "ON portfolio_ask_turns (intent)"
        )
    )
