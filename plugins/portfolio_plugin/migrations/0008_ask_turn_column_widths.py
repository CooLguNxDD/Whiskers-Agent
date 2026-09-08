"""Portfolio plugin — cap portfolio_ask_turns visitor-controlled strings.

Public CatPortfolio traffic writes this table via the ask key. Unbounded
TEXT on session/intent/view/slug/ids lets a visitor inflate a row; clip
in-place to GraphRunEvent-style VARCHAR widths (left(col, n) so existing
rows migrate rather than fail).

Plugin: portfolio_plugin
Revision: 0008_ask_turn_column_widths

Applied by PluginSchemaMigrator — receives a live SQLAlchemy connection.
See: db_layer/plugin_schema_migrator.py
"""

from __future__ import annotations

from sqlalchemy import text


def upgrade(conn) -> None:
    """Shrink visitor-controlled columns to VARCHAR(n), clipping existing values."""
    conn.execute(
        text(
            """
            ALTER TABLE portfolio_ask_turns
                ALTER COLUMN run_id TYPE VARCHAR(64) USING left(run_id, 64),
                ALTER COLUMN subject TYPE VARCHAR(256) USING left(subject, 256),
                ALTER COLUMN visitor_session_id TYPE VARCHAR(64)
                    USING left(visitor_session_id, 64),
                ALTER COLUMN intent TYPE VARCHAR(64) USING left(intent, 64),
                ALTER COLUMN view TYPE VARCHAR(64) USING left(view, 64),
                ALTER COLUMN focus_slug TYPE VARCHAR(256) USING left(focus_slug, 256),
                ALTER COLUMN parent_run_id TYPE VARCHAR(64) USING left(parent_run_id, 64)
            """
        )
    )
