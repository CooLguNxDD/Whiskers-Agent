"""Protocol interface for route registration and lifecycle management."""

from typing import Protocol, Any, Iterable


class IRouteRegistry(Protocol):
    """Protocol defining route registration and lookup capabilities."""

    def contribute(self, routes: Iterable[Any]) -> int:
        """Contribute an iterable of route descriptors to the registry."""
        ...

    def register_binding(self, binding: Any) -> None:
        """Register a late-bound route binding with the registry."""
        ...

    def remove_plugin(self, plugin_id: str) -> int:
        """Remove all registered routes contributed by a specific plugin ID."""
        ...
