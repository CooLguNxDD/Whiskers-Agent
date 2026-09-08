"""Database engine, sync session factory (Alembic), and async session factory (runtime)."""

import os
import sys
import asyncio
import threading
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from db_layer.models import Base
 
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# MASTER_KEY — required when DATABASE_URL is set, so pgcrypto functions work.
# Read lazily so processes that load .env after importing this module still
# pick up the final configured value before opening DB connections.
# ---------------------------------------------------------------------------


def _get_master_key() -> str:
    return os.environ.get("MASTER_KEY", "").strip()


def _validate_master_key(context: str = "runtime") -> None:
    """Raise RuntimeError if MASTER_KEY is absent when the DB is configured."""
    if not _get_master_key():
        raise RuntimeError(
            f"MASTER_KEY environment variable is required when DATABASE_URL is set "
            f"(context: {context}). Set MASTER_KEY to a 32-byte base64 string "
            f"(e.g. export MASTER_KEY=$(openssl rand -base64 32))."
        )


def _make_master_key_listener():
    """Return a DBAPI-level connect event handler that injects MASTER_KEY as a GUC.

    The handler uses a plain cursor execute (not SQLAlchemy text) because the
    psycopg3 async driver fires DBAPI-level connect events synchronously on the
    underlying sync connection before the async wrapper takes over.
    """
    key = _get_master_key()  # capture the final env value at engine creation time

    def _set_master_key(dbapi_connection, connection_record):  # noqa: ARG001
        if key:
            cursor = dbapi_connection.cursor()
            # Use the parameterised set_config() to avoid any injection risk.
            cursor.execute("SELECT set_config('app.master_key', %s, false)", (key,))
            cursor.close()

    return _set_master_key


def get_database_url() -> str:
    """
    Retrieves the configured database URL.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    # If running locally (not inside a Docker container), translate internal host 'postgres' to 'localhost'
    if not os.path.exists("/.dockerenv"):
        url = url.replace("@postgres:", "@localhost:").replace("//postgres:", "//localhost:")
    return url


def _to_async_url(url: str) -> str:
    """Normalize a DATABASE_URL to use the psycopg 3 async driver."""
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg://" + url[len("postgresql+psycopg2://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    # Already postgresql+psycopg:// or postgresql+asyncpg:// — keep as-is
    return url


# ── Sync engine (used by Alembic migrations and init_db) ───────────────────────

def create_db_engine(database_url: str | None = None):
    """
    Creates the database engine.
    """
    url = database_url or get_database_url()
    _validate_master_key("sync engine")
    engine = create_engine(url, pool_pre_ping=True)
    event.listen(engine, "connect", _make_master_key_listener())
    return engine


def create_session_factory(database_url: str | None = None):
    """
    Creates a database session factory.
    """
    engine = create_db_engine(database_url)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db(database_url: str | None = None) -> None:
    """Create all tables if they do not already exist (dev/test helper).

    Also applies any additive idempotent column migrations that cannot be
    expressed by create_all alone (which does not ALTER existing tables).
    """
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    # Portfolio DDL (including context_sources) is owned by
    # plugins/portfolio_plugin/migrations/ — do not ALTER plugin tables from core.


# ── Async engine + session (used by embedding operations and cache) ─────────────

_async_engine = None
_async_session_factory: async_sessionmaker | None = None
_async_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}
_async_locks_guard = threading.Lock()


def _async_lock_for_loop() -> asyncio.Lock:
    """Return loop-bound asyncio.Lock for the current running event loop."""
    loop = asyncio.get_running_loop()
    with _async_locks_guard:
        if loop not in _async_locks:
            _async_locks[loop] = asyncio.Lock()
        return _async_locks[loop]


async def _get_async_session_factory() -> async_sessionmaker:
    """Async-safe lazy initialisation of the async session factory.

    Uses the process-wide _async_locks_guard (threading.Lock) to guard the
    shared _async_engine / _async_session_factory globals so that coroutines
    running on *different* event loops cannot race past the None check and
    create duplicate engines.
    """
    global _async_engine, _async_session_factory
    # Fast path: already initialised — no lock needed (immutable after set).
    if _async_session_factory is not None:
        return _async_session_factory
    with _async_locks_guard:
        # Re-check inside the lock in case another thread just finished init.
        if _async_session_factory is not None:
            return _async_session_factory
        try:
            _validate_master_key("async engine")
            url = _to_async_url(get_database_url())
            engine = create_async_engine(url, pool_pre_ping=True)
            # Attach the MASTER_KEY listener to the underlying sync engine.
            # psycopg3 fires the DBAPI-level "connect" event synchronously
            # on the raw connection before the async wrapper activates.
            event.listen(
                engine.sync_engine,
                "connect",
                _make_master_key_listener(),
            )
            # expire_on_commit=False prevents SQLAlchemy from expiring attributes on commit,
            # avoiding post-commit lazy load attempts and MissingGreenletError across async handlers.
            factory = async_sessionmaker(
                bind=engine,
                expire_on_commit=False,
            )
            # Atomically publish both globals together while still holding the lock.
            _async_engine = engine
            _async_session_factory = factory
        except Exception:
            _async_engine = None
            _async_session_factory = None
            raise
    return _async_session_factory


async def _ensure_master_key(session: AsyncSession) -> None:
    """Set app.master_key GUC if not already present on this connection."""
    key = _get_master_key()
    if not key:
        return
    result = await session.execute(select(func.current_setting("app.master_key", True)))
    if not result.scalar():
        await session.execute(select(func.set_config("app.master_key", key, False)))


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager that yields a SQLAlchemy AsyncSession."""
    factory = await _get_async_session_factory()
    async with factory() as session:
        await _ensure_master_key(session)
        yield session


async def dispose_async_engine() -> None:
    """Async-safe clean disposal of the async database engine.

    Atomically detaches the global engine references under _async_locks_guard
    before awaiting dispose(), so no other coroutine (on any event loop) can
    observe or use a half-disposed engine.  The actual async dispose() call
    happens outside the threading lock to avoid blocking other threads during
    async I/O.
    """
    global _async_engine, _async_session_factory
    with _async_locks_guard:
        # Detach globals atomically; callers that acquired the lock after us
        # will see None and skip initialisation / disposal.
        engine_to_dispose = _async_engine
        _async_engine = None
        _async_session_factory = None
    # Await the actual pool shutdown outside the threading lock.
    if engine_to_dispose is not None:
        await engine_to_dispose.dispose()

