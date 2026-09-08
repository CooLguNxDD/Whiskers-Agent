"""
Enum for standardizing plugin authentication status.
"""
from enum import Enum

class AuthStatus(str, Enum):
    """
    Authentication status representation.
    """
    OK = "ok"
    NEEDS_REAUTH = "needs_reauth"
