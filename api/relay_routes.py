"""



Relay routing API endpoints.

Endpoints
---------
GET    /api/relay/session_gated/status   — Check if the global Layer 2 OAuth relay is configured and ready.
"""
import logging
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from utils.error_response import safe_error_response

from core.context import http_route_registry
import core.context

from core.http_route_registry import AuthPolicy

logger = logging.getLogger("whiskers")

@http_route_registry.route(
    route="relay",
    endpoint="status",
    methods=["GET"],
    name="api_relay_status",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.relay",
)
async def get_relay_status(request: Request) -> Response:
    """Check if the global Layer 2 OAuth relay is configured and ready."""
    try:
        is_configured = core.context.oauth_relay is not None
        return JSONResponse({"configured": is_configured}, status_code=200)
    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/relay/status GET error")
