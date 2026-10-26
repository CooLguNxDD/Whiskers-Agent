"""Job-search tenancy helper.

Tools never accept a caller-supplied ``tenant_id``; they resolve the
authenticated principal here and fail closed when it is unresolved.
"""

from __future__ import annotations


def require_tenant_id() -> int | None:
    """Return the principal's positive tenant id, or None if missing/invalid."""
    from core.context import current_tenant_id

    raw = current_tenant_id.get()
    try:
        tid = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        tid = 0
    return tid if tid > 0 else None
