"""Route/tool registries bound to the FastMCP app, plus per-request org context.
Split out of core/context.py (Phase 1 modularity refactor)."""
from contextvars import ContextVar

from core.context._app import mcp
from core.route_registry import RouteRegistry, HttpRouteRegistry, _set_http_route_registry
from core.proxy_tools.tool_visibility import ToolVisibility

route_registry = RouteRegistry()

http_route_registry = HttpRouteRegistry(mcp)
http_route_registry.seed_default_policies()
_set_http_route_registry(http_route_registry)

tool_visibility = ToolVisibility(mcp)

current_org_id: ContextVar[str] = ContextVar("current_org_id", default="default")
current_tenant_id: ContextVar[int] = ContextVar("current_tenant_id", default=1)

