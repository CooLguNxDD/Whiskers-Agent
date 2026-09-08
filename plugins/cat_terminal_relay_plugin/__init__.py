"""
Cat Terminal Relay Plugin — reverse WebSocket relay for remote interactive
terminals hosted in a VS Code extension on the host machine.

The PTY lives on the host (inside the extension); Cat Tunnel is a pure byte
relay. Security spine = a command allowlist (command_guard) + Layer-1 JWT scopes
(terminal:use / terminal:host) + a short-lived Layer-2 terminal token.
"""

import logging

from .plugin_config import CatTerminalRelayPlugin

logger = logging.getLogger("whiskers.plugins")


def register(registry):
    """Register cat_terminal_relay_plugin (tools + lifecycle)."""
    try:
        # Inject config.json env defaults BEFORE importing MCPTools, so command_guard's
        # import-time allowlist/privileged reads see them (host env still wins).
        from .config_loader import load_env_defaults
        load_env_defaults()
        from plugins.cat_terminal_relay_plugin import MCPTools  # noqa: F401
        registry.lifecycle.register_plugin(CatTerminalRelayPlugin())
        logger.info("cat_terminal_relay_plugin registered.")
    except Exception as e:
        logger.error(f"Error registering cat_terminal_relay_plugin: {e}", exc_info=True)
        raise


__all__ = []
