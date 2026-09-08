"""Environment-derived flags and the DB-conditional helper.
Split out of core/context.py (Phase 1 modularity refactor) — no side effects beyond env reads."""
import os
import inspect

_oauth_env = os.environ.get("OAUTH_ENABLED", "").lower()
OAUTH_ENABLED = _oauth_env == "true"

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:10000")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")
_DB_AVAILABLE = bool(os.environ.get("DATABASE_URL", "").strip())


async def run_if_db_available(func, *args, **kwargs):
    """
    Functional pattern to execute a DB operation only if available.
    Takes either a callable or a string name of a function in db_layer.
    """
    if not _DB_AVAILABLE:
        return None

    target = func
    if isinstance(func, str):
        import db_layer
        target = getattr(db_layer, func)

    if inspect.iscoroutinefunction(target):
        return await target(*args, **kwargs)
    return target(*args, **kwargs)
