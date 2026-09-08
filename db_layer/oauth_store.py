"""
OAuth client and access-token persistence backed by PostgreSQL.

These functions are the DB-layer counterparts to the in-memory/JSON stores
in the legacy OAuth provider.  They are called only when DATABASE_URL is set;
every function swallows its own exceptions so a DB outage never breaks the
OAuth flow.
"""

import logging
import time

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from mcp.server.auth.provider import AccessToken
from mcp.shared.auth import OAuthClientInformationFull

from .connection import get_async_session
from .models import OAuthAccessToken, OAuthClient

logger = logging.getLogger("whiskers")


async def db_save_client(client_info: OAuthClientInformationFull) -> None:
    """Upsert an OAuth client record."""
    try:
        async with get_async_session() as session:
            stmt = insert(OAuthClient).values(
                client_id=client_info.client_id,
                client_data=client_info.model_dump(mode="json"),
            ).on_conflict_do_update(
                index_elements=["client_id"],
                set_={"client_data": client_info.model_dump(mode="json")},
            )
            await session.execute(stmt)
            await session.commit()
        logger.debug(f"DB: upserted OAuth client {client_info.client_id!r}")
    except Exception as exc:
        logger.warning(f"DB save client failed (JSON fallback in use): {exc}")


async def db_load_client(client_id: str) -> OAuthClientInformationFull | None:
    """Return the client for *client_id*, or None if not found."""
    try:
        async with get_async_session() as session:
            row = await session.get(OAuthClient, client_id)
            if row:
                return OAuthClientInformationFull.model_validate(row.client_data)
    except Exception as exc:
        logger.warning(f"DB load client failed: {exc}")
    return None


async def db_save_token(token: AccessToken) -> None:
    """Upsert an access token record."""
    try:
        async with get_async_session() as session:
            stmt = insert(OAuthAccessToken).values(
                token=token.token,
                client_id=token.client_id,
                scopes=list(token.scopes) if token.scopes else [],
                expires_at=token.expires_at,
            ).on_conflict_do_update(
                index_elements=["token"],
                set_={"expires_at": token.expires_at},
            )
            await session.execute(stmt)
            await session.commit()
        logger.debug(f"DB: persisted access token for client {token.client_id!r}")
    except Exception as exc:
        logger.warning(f"DB save token failed (token lives in memory only): {exc}")


async def db_load_token(token: str) -> AccessToken | None:
    """Return the AccessToken for *token*, or None if not found / expired."""
    try:
        async with get_async_session() as session:
            row = await session.get(OAuthAccessToken, token)
            if row:
                at = AccessToken(
                    token=row.token,
                    client_id=row.client_id,
                    scopes=row.scopes or [],
                    expires_at=row.expires_at,
                )
                # Treat DB-stored expired tokens as missing
                if at.expires_at and at.expires_at < time.time():
                    await db_delete_token(token)
                    return None
                return at
    except Exception as exc:
        logger.warning(f"DB load token failed: {exc}")
    return None


async def db_delete_token(token: str) -> None:
    """Delete an access token (on revocation or expiry)."""
    try:
        async with get_async_session() as session:
            await session.execute(
                delete(OAuthAccessToken).where(OAuthAccessToken.token == token)
            )
            await session.commit()
    except Exception as exc:
        logger.warning(f"DB delete token failed: {exc}")
