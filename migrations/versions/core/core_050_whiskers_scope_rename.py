"""Rebrand cutover: rewrite the ``core:tunnel.*`` scope domain to ``core:whiskers.*``.

Revision ID: core_050
Revises: core_049
Create Date: 2026-09-07

``tunnel`` was the core domain for the admin-console surface under the
product's previous name. The product is Whiskers Agent now, so the
domain becomes ``whiskers`` — a pure prefix rename inside an already
grammar-shaped token (``core:<domain>[.<sub>]:<access>``), not a legacy
free-token migration like ``core_047_scope_cutover``.

Same four storages / three representations as ``core_047`` (see its docstring):

- ``api_keys.scopes`` / ``api_key_scope_presets.scopes`` — JSONB array (NULL is
  a distinct "legacy full access" sentinel, left untouched)
- ``oauth_tokens.scopes`` — TEXT, space-joined
- ``oauth_auth_codes.scopes_granted`` — TEXT, space-joined
- ``mcp_bearer_tokens.scopes`` — ARRAY(TEXT)

Unlike ``core_047`` this migration is lossless and exactly invertible: every
``core:tunnel``-prefixed token maps to exactly one ``core:whiskers`` token and
back, and no token is dropped. Non-matching tokens pass through untouched.

The rename is a hard cut — there is no read-side alias accepting the old
domain, so any API key, OAuth token or bearer token minted before this
migration must be rewritten by it (that is what ``upgrade()`` does) or
re-issued. Keep this file's inlined prefixes frozen: ``core_0NN`` migrations
never import from ``core/``, to stay decoupled from app import-time side
effects.
"""

import json

from alembic import op
import sqlalchemy as sa

revision = "core_050"
down_revision = "core_049"
branch_labels = None
depends_on = None


_OLD_PREFIX = "core:tunnel"
_NEW_PREFIX = "core:whiskers"


def _rename(token: str, old: str, new: str) -> str:
    """Rewrite ``old`` domain prefix to ``new``; leave every other token alone.

    Matches the bare domain (``core:tunnel:write``) and any sub-domain
    (``core:tunnel.proxy:read``), never a longer sibling domain that merely
    starts with the same letters.
    """
    if token == old or token.startswith(f"{old}.") or token.startswith(f"{old}:"):
        return new + token[len(old):]
    return token


def _map_list(tokens: list) -> list:
    out: list = []
    for t in tokens:
        mapped = _rename(t, _OLD_PREFIX, _NEW_PREFIX)
        if mapped not in out:
            out.append(mapped)
    return out


def _reverse_map_list(tokens: list) -> list:
    out: list = []
    for t in tokens:
        mapped = _rename(t, _NEW_PREFIX, _OLD_PREFIX)
        if mapped not in out:
            out.append(mapped)
    return out


def _migrate_jsonb_column(conn, table: str, id_col: str, scope_col: str, mapper) -> None:
    rows = conn.execute(sa.text(f"SELECT {id_col}, {scope_col} FROM {table}")).fetchall()
    for row_id, scopes in rows:
        if scopes is None or not isinstance(scopes, list):
            continue  # NULL is the legacy-full-access sentinel — untouched
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
        new_text = " ".join(mapper(tokens))
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


def _apply(mapper) -> None:
    conn = op.get_bind()
    _migrate_jsonb_column(conn, "api_keys", "id", "scopes", mapper)
    _migrate_jsonb_column(conn, "api_key_scope_presets", "id", "scopes", mapper)
    _migrate_text_column(conn, "oauth_tokens", "jti", "scopes", mapper)
    _migrate_text_column(conn, "oauth_auth_codes", "id", "scopes_granted", mapper)
    _migrate_array_column(conn, "mcp_bearer_tokens", "token", "scopes", mapper)


def upgrade() -> None:
    """``core:tunnel*`` -> ``core:whiskers*`` across every stored scope column."""
    _apply(_map_list)


def downgrade() -> None:
    """Exact inverse — the rename is lossless in both directions."""
    _apply(_reverse_map_list)
