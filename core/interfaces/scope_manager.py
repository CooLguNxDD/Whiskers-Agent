"""Protocol interface for scope rule registration and validation."""

from typing import Protocol, Any


class IScopeManager(Protocol):
    """Protocol defining the scope management interface."""

    def register_rule(self, rule: Any) -> None:
        """Register an authorization scope rule with the manager."""
        ...
