"""Protocol interface for upstream MCP proxy lifecycle and management."""

from typing import Protocol, Any, Dict, List


class IProxyManager(Protocol):
    """Protocol defining the management interface for upstream MCP proxies."""

    async def get_oauth_status(self, name: str) -> str:
        """Return the current OAuth connection status for an upstream proxy."""
        ...

    async def discover_oauth_metadata(self, url: str, name: str) -> dict:
        """Discover OAuth 2.0 authorization and token endpoints for an upstream proxy."""
        ...

    async def enable_proxy(self, name: str) -> None:
        """Enable and mount a previously configured upstream proxy server."""
        ...

    async def disable_proxy(self, name: str) -> None:
        """Disable and unmount an active upstream proxy without deleting its record."""
        ...

    async def load_persisted(self) -> None:
        """Load and mount all persisted proxy definitions from database storage."""
        ...

    async def list_proxies(self) -> List[Dict[str, Any]]:
        """List all configured upstream proxies and their current runtime statuses."""
        ...

    async def add_proxy(
        self,
        name: str,
        transport: str,
        url: str,
        auth_mode: str = "none",
        bearer_token: Any = None,
        oauth_config: Any = None,
        client_secret: Any = None,
        custom_description: Any = None,
    ) -> Dict[str, Any]:
        """Register, persist, mount, and discover tools for a new upstream proxy."""
        ...

    async def remove_proxy(self, name: str) -> Dict[str, Any]:
        """Unmount and delete an upstream proxy and its stored credentials."""
        ...

    async def test_proxy(self, name: str) -> Dict[str, Any]:
        """Test upstream connectivity, rediscover tools, and update database status."""
        ...

    async def update_proxy_description(
        self, name: str, custom_description: str | None
    ) -> Dict[str, Any]:
        """Update the custom description or metadata for an upstream proxy."""
        ...
