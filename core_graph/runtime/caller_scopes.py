"""Shared caller-scope resolver for specialist/flow dispatch.

Was duplicated in ``core_graph.node.specialist_entry`` and
``core_graph.subgraphs.specialist.flow_registry``. Both threaded scopes from
the ``get_request_principal()`` contextvar only, which is set exclusively by
``api/playground_routes.py``. On the MCP HTTP transport that contextvar is
never set, so every FlowSpec-dispatched deterministic stage fell back to
``caller_scopes=[]`` (fail-closed) even when the caller's real, validated
token scopes were already sitting in ``state["caller_scopes"]``
(``core_graph.states``, seeded by ``core_graph.runtime.mode_router`` from the
MCP access token — see ``core_graph.mcp_tool._caller_scopes``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("whiskers.core_graph.specialist")

# Sentinel: distinguish "no state scopes argument passed" from an explicit
# state value of None (unauthenticated / stdio — must NOT be treated as "no
# state available" and skipped).
_UNSET = object()


def _stdio_safe() -> bool:
    """True when local stdio transport allows unrestricted CLI scopes."""
    try:
        from core.context.transport import is_local_stdio

        return is_local_stdio()
    except Exception:
        return False


def resolve_caller_scopes(state_scopes: object = _UNSET) -> list[str] | None:
    """Resolve caller scopes for a specialist/flow dispatch.

    Precedence:
      1. ``state_scopes`` is a list -> use it verbatim. This is the graph
         state value seeded from the caller's validated MCP access token
         (``None`` when unauthenticated), so a list here can never grant more
         than the token itself already holds.
      2. else the request-principal contextvar (playground REST path).
      3. else local stdio -> ``None`` (unrestricted CLI).
      4. else -> ``[]`` (fail closed; anonymous HTTP).

    Exceptions anywhere in resolution fail closed the same way branch 4 does.
    """
    if isinstance(state_scopes, list):
        logger.debug("caller_scopes resolved from graph state (%d scopes)", len(state_scopes))
        return list(state_scopes)

    try:
        from core.scope_management import get_request_principal

        principal = get_request_principal()
        if principal is not None:
            logger.debug("caller_scopes resolved from request principal")
            return list(principal.scopes) if principal.scopes is not None else []
        if _stdio_safe():
            logger.debug("caller_scopes resolved: local stdio (unrestricted)")
            return None
        logger.debug("caller_scopes resolved: deny (no state, no principal, not stdio)")
        return []
    except Exception:
        return [] if not _stdio_safe() else None
