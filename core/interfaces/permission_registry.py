"""Protocol interface for plugin tool permissions registration."""

from typing import Protocol, Any


class IPermissionRegistry(Protocol):
    """Protocol defining permission registration for plugins."""

    def register_plugin_permissions(
        self, plugin_id: str, entries: Any, *, replace: bool = True
    ) -> Any:
        """Register or update permission rules for tools contributed by a plugin."""
        ...
