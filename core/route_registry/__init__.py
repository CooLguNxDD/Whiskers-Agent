"""Consolidated Route Registry package.

Combines HTTP/WebSocket route registration (HttpRouteRegistry) with
agent-facing/plugin-facing route registration (RouteRegistry) and the
live OperationCatalog contract layer.
"""

from core.route_registry.http_route_registry import (
    AuthPolicy,
    HttpRouteRegistry,
    RouteDeclaration,
    _set_http_route_registry,
    get_http_route_registry,
)
from core.route_registry.route_registry import RouteRegistry
from core.route_registry.route_descriptor import InstanceBinding, RouteDescriptor
from core.route_registry.path_builder import build_api_path, parse_api_gate
from core.route_registry.operation_descriptor import (
    AccessClass,
    Visibility,
    HttpExposure,
    McpExposure,
    UiContribution,
    OperationDescriptor,
)
from core.route_registry.operation_catalog import (
    OperationCatalog,
    get_operation_catalog,
)

__all__ = [
    "AuthPolicy",
    "HttpRouteRegistry",
    "RouteDeclaration",
    "get_http_route_registry",
    "_set_http_route_registry",
    "RouteRegistry",
    "RouteDescriptor",
    "InstanceBinding",
    "build_api_path",
    "parse_api_gate",
    "AccessClass",
    "Visibility",
    "HttpExposure",
    "McpExposure",
    "UiContribution",
    "OperationDescriptor",
    "OperationCatalog",
    "get_operation_catalog",
]
