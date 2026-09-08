"""Portfolio tenancy helpers.

**Single-tenant assumption:** public portfolio surfaces (anonymous layout
reads, baked assets) always resolve against ``PORTFOLIO_TENANT_ID``. Write
tools must not accept a caller-supplied ``tenant_id`` — they resolve the
authenticated principal via ``require_tenant_id()`` and fail closed when
unresolved.
"""

from __future__ import annotations

# Single-tenant by design (public HR layouts + baked assets). Multi-tenant
# portfolio is not planned; do not thread caller tenant_id onto public routes.
PORTFOLIO_TENANT_ID = 1


def require_tenant_id() -> int | None:
    """Return the authenticated principal's tenant id, or None if unresolved.

    Fail-closed for write tools: never invent a tenant from a caller argument.
    Mirrors bake/patch tools — principal context only.
    """
    from core.context import current_tenant_id

    raw = current_tenant_id.get()
    try:
        tid = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        tid = 0
    return tid if tid > 0 else None
