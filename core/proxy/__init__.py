"""Whiskers Agent upstream MCP proxy infrastructure."""

from .proxy_manager import proxy_manager
from .compose import build_proxy, mount_proxy, unmount_proxy

__all__ = [
    "proxy_manager",
    "build_proxy",
    "mount_proxy",
    "unmount_proxy",
]
