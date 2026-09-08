"""core/context/scope_middleware — Scope enforcement middleware for direct FastMCP tool calls.

Secures plugin tools by validating caller scopes via ScopeManager.evaluate.
"""

import logging
import os
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.dependencies import get_access_token

from core.scope_management import (
    PrincipalKind,
    ScopeGrant,
    evaluate_access,
    get_request_principal,
    REASON_DENY_ANONYMOUS,
)
from core.proxy_tools.tool_visibility import GATEWAY_ALWAYS_VISIBLE

logger = logging.getLogger("whiskers")


async def _resolve_plugin_id_and_tags(context: MiddlewareContext, name: str) -> tuple[str, tuple]:
    """Resolve a tool's plugin_id + tags for scope gating — local lookups only.

    Delegates to ``core.context.tool_meta.resolve_tool_meta``, which never calls
    ``app.get_tool``/``list_tools`` on the composite FastMCP server: that fans out
    to every mounted proxy provider (and, for disabled/hidden tools, forces a full
    upstream re-list). See that module's docstring for the resolution ladder.
    """
    from core.context.tool_meta import resolve_tool_meta

    meta = await resolve_tool_meta(context, name)
    return meta.plugin_id, meta.tags


def _grant_from_request() -> ScopeGrant | None:
    """Build ScopeGrant from access token or request principal contextvar.

    Returns None when there is no token/principal and transport is local stdio
    (CLI unrestricted). Network with no principal → ANONYMOUS grant (deny).
    """
    from core.context.transport import is_local_stdio

    token = get_access_token()
    if token is not None:
        scopes = list(token.scopes) if token.scopes is not None else []
        kind = PrincipalKind.API_KEY
        sub = getattr(token, "client_id", None) or getattr(token, "subject", None) or ""
        if isinstance(sub, str) and not str(sub).startswith("octk_"):
            kind = PrincipalKind.OAUTH_CLIENT
        return ScopeGrant(scopes=scopes, role=None, kind=kind)

    principal = get_request_principal()
    if principal is not None:
        return principal

    if is_local_stdio():
        return None  # unrestricted CLI

    return ScopeGrant(scopes=[], role=None, kind=PrincipalKind.ANONYMOUS)


class ScopeEnforcementMiddleware(Middleware):
    """FastMCP middleware that checks API-key scopes on direct tool calls."""

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: Any
    ) -> Any:
        """Gate direct tool calls based on the access token scopes.

        Raises ToolError if scopes do not match required route scopes.
        """
        from core.context.transport import is_local_stdio
        if os.environ.get("DANGEROUSLY_SKIP_PERMISSIONS") == "true" and is_local_stdio():
            logger.warning("DANGEROUSLY_SKIP_PERMISSIONS is enabled (stdio only). Bypassing middleware scope checks.")
            return await call_next(context)

        name = context.message.name

        if name in GATEWAY_ALWAYS_VISIBLE:
            return await call_next(context)

        grant = _grant_from_request()
        if grant is None:
            # Local stdio, no token — unrestricted
            return await call_next(context)

        plugin_id, tags = await _resolve_plugin_id_and_tags(context, name)
        decision = evaluate_access(
            grant,
            plugin_id=plugin_id,
            tags=tags,
            tool_name=name,
            path="direct_tool",
        )
        if not decision.allowed:
            if decision.reason == REASON_DENY_ANONYMOUS:
                raise ToolError("authentication required")
            raise ToolError(
                f"Scope denied for {name} (requires one of {sorted(decision.required)})."
            )

        return await call_next(context)
