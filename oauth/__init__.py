"""
Whiskers Agent OAuth package — Layer 1 provider and callback routes.
Loaded conditionally when OAUTH_ENABLED is True.
"""

# Explicit imports for public API (loaded conditionally in MCPTools)
from .oauth_provider import LegacyOAuthProvider

__all__ = ["LegacyOAuthProvider"]