"""Extend the legacy-cutover map with sandbox:exec -> core:terminal.sandbox:write.

Revision ID: core_048
Revises: core_047
Create Date: 2026-08-18

``sandbox:exec`` was never in ``core_047``'s inlined ``LEGACY_SCOPE_MAP`` (it
was discovered only after that migration was authored), so any row already
rewritten by ``core_047`` had a literal ``sandbox:exec`` token silently
dropped rather than mapped — that specific value cannot be recovered here
(it is simply absent from the array now), matching ``core_047``'s own
documented "unmapped tokens dropped, fail-closed" precedent.

What this migration *does* fix: any row that still holds the literal
``sandbox:exec`` string today (a DB where ``core_047`` hasn't run yet, or one
that was downgraded back through it) gets it mapped forward to
``core:terminal.sandbox:write`` here, using the same four-storage helpers as
``core_047``. Keep this file's inlined map in sync with
``core.scope_management.legacy_map.LEGACY_SCOPE_MAP`` if it changes again.
"""

from alembic import op
import sqlalchemy as sa

revision = "core_048"
down_revision = "core_047"
branch_labels = None
depends_on = None


_LEGACY_TOKEN = "sandbox:exec"
_NEW_TOKEN = "core:terminal.sandbox:write"


def _map_token(token: str) -> str:
    return _NEW_TOKEN if token == _LEGACY_TOKEN else token


def _map_list(tokens: list) -> list:
    out: list = []
    for t in tokens:
        mapped = _map_token(t)
        if mapped not in out:
            out.append(mapped)
    return out


def _reverse_map_token(token: str) -> str:
    return _LEGACY_TOKEN if token == _NEW_TOKEN else token


def _reverse_map_list(tokens: list) -> list:
    out: list = []
    for t in tokens:
        mapped = _reverse_map_token(t)
        if mapped not in out:
            out.append(mapped)
    return out


def _migrate_jsonb_column(conn, table: str, id_col: str, scope_col: str, mapper) -> None:
    import json

    rows = conn.execute(sa.text(f"SELECT {id_col}, {scope_col} FROM {table}")).fetchall()
    for row_id, scopes in rows:
        if scopes is None or not isinstance(scopes, list):
            continue
        new_scopes = mapper(scopes)
        if new_scopes != scopes:
            conn.execute(
                sa.text(f"UPDATE {table} SET {scope_col} = CAST(:scopes AS jsonb) WHERE {id_col} = :id"),
                {"scopes": json.dumps(new_scopes), "id": row_id},
            )


def _migrate_text_column(conn, table: str, id_col: str, scope_col: str, mapper) -> None:
    rows = conn.execute(sa.text(f"SELECT {id_col}, {scope_col} FROM {table}")).fetchall()
    for row_id, scopes_text in rows:
        tokens = (scopes_text or "").split()
        new_tokens = mapper(tokens)
        new_text = " ".join(new_tokens)
        if new_text != (scopes_text or ""):
            conn.execute(
                sa.text(f"UPDATE {table} SET {scope_col} = :scopes WHERE {id_col} = :id"),
                {"scopes": new_text, "id": row_id},
            )


def _migrate_array_column(conn, table: str, id_col: str, scope_col: str, mapper) -> None:
    rows = conn.execute(sa.text(f"SELECT {id_col}, {scope_col} FROM {table}")).fetchall()
    for row_id, scopes in rows:
        tokens = list(scopes or [])
        new_tokens = mapper(tokens)
        if new_tokens != tokens:
            conn.execute(
                sa.text(f"UPDATE {table} SET {scope_col} = :scopes WHERE {id_col} = :id"),
                {"scopes": new_tokens, "id": row_id},
            )


def upgrade() -> None:
    """Rewrite any surviving literal sandbox:exec token to core:terminal.sandbox:write."""
    conn = op.get_bind()
    _migrate_jsonb_column(conn, "api_keys", "id", "scopes", _map_list)
    _migrate_jsonb_column(conn, "api_key_scope_presets", "id", "scopes", _map_list)
    _migrate_text_column(conn, "oauth_tokens", "jti", "scopes", _map_list)
    _migrate_text_column(conn, "oauth_auth_codes", "id", "scopes_granted", _map_list)
    _migrate_array_column(conn, "mcp_bearer_tokens", "token", "scopes", _map_list)


def downgrade() -> None:
    """Invert: core:terminal.sandbox:write -> sandbox:exec."""
    conn = op.get_bind()
    _migrate_jsonb_column(conn, "api_keys", "id", "scopes", _reverse_map_list)
    _migrate_jsonb_column(conn, "api_key_scope_presets", "id", "scopes", _reverse_map_list)
    _migrate_text_column(conn, "oauth_tokens", "jti", "scopes", _reverse_map_list)
    _migrate_text_column(conn, "oauth_auth_codes", "id", "scopes_granted", _reverse_map_list)
    _migrate_array_column(conn, "mcp_bearer_tokens", "token", "scopes", _reverse_map_list)
