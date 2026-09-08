"""WebSocket route registration for the Cat Terminal Relay plugin."""

from .console_ws import PATH as CONSOLE_PATH, console_ws
from .extension_ws import PATH as EXTENSION_PATH, extension_ws
from .data_ws import PATH as DATA_PATH, data_ws
from .hosts_ws import PATH as HOSTS_WS_PATH, hosts_ws
from .control_routes import register_control_routes


def register_routes() -> None:
    """Append the relay legs + REST control plane to the core route registries.

    Four WS legs: the browser console (``console_ws``), the extension control
    channel (``extension_ws``), the per-session extension data channel
    (``data_ws``), and the hosts status channel (``hosts_ws``). Plus the
    cookie-authed ``/api/terminal/*`` REST control plane.
    """
    from core.context import http_route_registry
    from core.http_route_registry import AuthPolicy

    http_route_registry.register_ws_route(CONSOLE_PATH, console_ws, name="terminal_console_ws", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.NONE)
    http_route_registry.register_ws_route(DATA_PATH, data_ws, name="terminal_data_ws", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.NONE)
    http_route_registry.register_ws_route(EXTENSION_PATH, extension_ws, name="terminal_extension_ws", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.NONE)
    http_route_registry.register_ws_route(HOSTS_WS_PATH, hosts_ws, name="terminal_hosts_ws", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.NONE)
    register_control_routes()


__all__ = ["register_routes"]
