"""Hard cutover: rewrite every stored scope token through LEGACY_SCOPE_MAP.

Revision ID: core_047
Revises: core_046
Create Date: 2026-08-18

Four storages, three representations (mirrors
``core.scope_management.legacy_map.LEGACY_SCOPE_MAP`` — keep both in sync if
that map changes):

- ``api_keys.scopes`` / ``api_key_scope_presets.scopes`` — JSONB array (NULL
  is a distinct "legacy full access" sentinel, left untouched)
- ``oauth_tokens.scopes`` — TEXT, space-joined
- ``oauth_auth_codes.scopes_granted`` — TEXT, space-joined
- ``mcp_bearer_tokens.scopes`` — ARRAY(TEXT)

Sentinels (``admin``/``all``/``*``) and already-grammar-shaped tokens
(``plugin:``/``group:``/``op:``/``core:``) pass through unchanged. Unmapped
legacy tokens are dropped (logged), matching ``core_032_drop_legacy_apikeys``'s
precedent that a fail-closed outcome is acceptable during a security
migration. A row that maps to an empty list is left as ``[]`` — already the
existing "deny all" meaning, not a new state.
"""

import json

from alembic import op
import sqlalchemy as sa

revision = "core_047"
down_revision = "core_046"
branch_labels = None
depends_on = None


LEGACY_SCOPE_MAP = {
    "terminal:use": "core:terminal:write",
    "terminal:host": "core:terminal:read",
    "whiskers": "core:graph:write",
}
UNCHANGED_PREFIXES = ("plugin:", "group:", "op:", "core:")
UNCHANGED_TOKENS = {"admin", "all", "*"}


def _map_token(token: str) -> str | None:
    if token in UNCHANGED_TOKENS or any(token.startswith(p) for p in UNCHANGED_PREFIXES):
        return token
    return LEGACY_SCOPE_MAP.get(token)


def _map_list(tokens: list) -> list:
    out: list = []
    for t in tokens:
        mapped = _map_token(t)
        if mapped is not None and mapped not in out:
            out.append(mapped)
    return out


def _reverse_map_token(token: str) -> str:
    for legacy, new in LEGACY_SCOPE_MAP.items():
        if new == token:
            return legacy
    return token


def _reverse_map_list(tokens: list) -> list:
    out: list = []
    for t in tokens:
        mapped = _reverse_map_token(t)
        if mapped not in out:
            out.append(mapped)
    return out


def _migrate_jsonb_column(conn, table: str, id_col: str, scope_col: str, mapper) -> None:
    rows = conn.execute(sa.text(f"SELECT {id_col}, {scope_col} FROM {table}")).fetchall()
    for row_id, scopes in rows:
        if scopes is None:
            continue  # legacy-full-access sentinel — untouched
        if not isinstance(scopes, list):
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
    """Rewrite legacy free-token scopes to level-1 grammar tokens in place."""
    conn = op.get_bind()
    _migrate_jsonb_column(conn, "api_keys", "id", "scopes", _map_list)
    _migrate_jsonb_column(conn, "api_key_scope_presets", "id", "scopes", _map_list)
    _migrate_text_column(conn, "oauth_tokens", "jti", "scopes", _map_list)
    _migrate_text_column(conn, "oauth_auth_codes", "id", "scopes_granted", _map_list)
    _migrate_array_column(conn, "mcp_bearer_tokens", "token", "scopes", _map_list)


def downgrade() -> None:
    """Invert the token map (best-effort; unmapped-and-dropped tokens can't be restored)."""
    conn = op.get_bind()
    _migrate_jsonb_column(conn, "api_keys", "id", "scopes", _reverse_map_list)
    _migrate_jsonb_column(conn, "api_key_scope_presets", "id", "scopes", _reverse_map_list)
    _migrate_text_column(conn, "oauth_tokens", "jti", "scopes", _reverse_map_list)
    _migrate_text_column(conn, "oauth_auth_codes", "id", "scopes_granted", _reverse_map_list)
    _migrate_array_column(conn, "mcp_bearer_tokens", "token", "scopes", _reverse_map_list)
