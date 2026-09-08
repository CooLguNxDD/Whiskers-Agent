"""Abstract protocols and interface definitions for core Whiskers Agent subsystems."""

from .proxy_manager import IProxyManager
from .route_registry import IRouteRegistry
from .scope_manager import IScopeManager
from .permission_registry import IPermissionRegistry
from .artifact_store import IArtifactStore
from .auth_service import IAuthService
from .principal import Principal

__all__ = [
    "IProxyManager",
    "IRouteRegistry",
    "IScopeManager",
    "IPermissionRegistry",
    "IArtifactStore",
    "IAuthService",
    "Principal",
]
