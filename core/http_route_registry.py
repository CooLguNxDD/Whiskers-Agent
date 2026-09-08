"""Shim module forwarding to core.route_registry.http_route_registry."""

from core.route_registry.http_route_registry import (
    AuthPolicy,
    HttpRouteRegistry,
    RouteDeclaration,
    _set_http_route_registry,
    get_http_route_registry,
)

__all__ = [
    "AuthPolicy",
    "HttpRouteRegistry",
    "RouteDeclaration",
    "get_http_route_registry",
    "_set_http_route_registry",
]
