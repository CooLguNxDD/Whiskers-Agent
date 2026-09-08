"""
Plugin-agnostic WebSocket + late-bound HTTP route registry.

DEPRECATION WARNING:
This module is a deprecation shim. Plugins should import and use
`http_route_registry` from `core.context`.

FastMCP's ``@mcp.custom_route`` only registers HTTP routes, and only those
imported before ``mcp.http_app()`` builds the Starlette app. Plugins load later
(during the boot pipeline lifespan), so they cannot use ``@mcp.custom_route`` and
have no notion of WebSocket endpoints at all. Such plugins append Starlette
``WebSocketRoute`` / ``Route`` objects here during ``on_load``; the entrypoint
extends the router built by ``mcp.http_app()`` with both lists, so the entrypoint
never imports any plugin directly.
"""

import logging
from typing import Awaitable, Callable

from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

logger = logging.getLogger("whiskers")

# Populated by plugins at on_load; consumed once by whiskers_agent_mcp.py.
WS_ROUTES: list[WebSocketRoute] = []
HTTP_ROUTES: list[Route] = []


def register_ws_route(
    path: str,
    endpoint: "Callable[[WebSocket], Awaitable[None]]",
    name: str | None = None,
) -> None:
    """Register a Starlette WebSocketRoute to be mounted on the HTTP app."""
    from core.context import http_route_registry
    http_route_registry.register_ws_route(path, endpoint, name=name, owner="legacy")


def register_http_route(
    path: str,
    endpoint: Callable,
    methods: list[str],
    name: str | None = None,
) -> None:
    """Register a late-bound Starlette HTTP Route to be mounted on the app."""
    from core.context import http_route_registry
    http_route_registry.register_http_route(path, endpoint, methods, name=name, owner="legacy")

