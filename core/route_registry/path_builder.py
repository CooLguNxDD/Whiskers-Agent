"""Utility functions for API path building and parsing."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.route_registry.http_route_registry import AuthPolicy


def build_api_path(route: str, auth_policy: "AuthPolicy", endpoint: str = "") -> str:
    """Compose /api/{route}/{auth_policy.value}/{endpoint}."""
    base = f"/api/{route.strip('/')}/{auth_policy.value}"
    ep = (endpoint or "").strip("/")
    return f"{base}/{ep}" if ep else base


def parse_api_gate(path: str) -> str | None:
    """Return gate segment for /api/{route}/{gate}/... paths, else None."""
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "api":
        gate = parts[2]
        if gate in ("public", "session_gated", "scope_required", "none"):
            return gate
    return None
