"""
Shared service singletons for the Cat Terminal Relay plugin.

Both the WebSocket routes and the MCP tools import these instances so they
operate on the same in-memory state.
"""

from plugins.cat_terminal_relay_plugin.plugin_config import SETTINGS

from .elevation_service import ElevationService
from .ide_registry import IdeRegistry
from .session_registry import (
    ABSOLUTE_TTL_SECONDS,
    IDLE_TTL_SECONDS,
    MAX_SESSIONS_PER_SUBJECT,
    SessionRegistry,
)
from .terminal_token_service import TerminalTokenService

token_service = TerminalTokenService()
session_registry = SessionRegistry(
    idle_ttl=SETTINGS.get("session_idle_ttl_seconds", IDLE_TTL_SECONDS),
    absolute_ttl=SETTINGS.get("session_absolute_ttl_seconds", ABSOLUTE_TTL_SECONDS),
    max_per_subject=SETTINGS.get("max_sessions_per_subject", MAX_SESSIONS_PER_SUBJECT),
)
ide_registry = IdeRegistry()
elevation_service = ElevationService(
    method=SETTINGS.get("step_up_method", "totp"),
    ttl_seconds=SETTINGS.get("elevation_ttl_seconds", 300),
    max_attempts=SETTINGS.get("max_attempts", 3),
    lockout_seconds=SETTINGS.get("lockout_seconds", 300),
)

# Clear elevation state whenever a session is killed (explicit or reaped).
session_registry.add_kill_hook(elevation_service.clear)

__all__ = [
    "token_service",
    "session_registry",
    "ide_registry",
    "elevation_service",
    "IdeRegistry",
    "SessionRegistry",
    "TerminalTokenService",
    "ElevationService",
]
