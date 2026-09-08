"""
Plugin Tier enumeration values used for permission tracking.
"""
import logging
from enum import IntEnum

logger = logging.getLogger("whiskers.plugins")

class Tier(IntEnum):
    """
    Plugin tier definitions.
    """
    LITE = 1
    PRO = 100
    ADMIN = 500
    TEST = 9999


def _tier_display_name(value: int) -> str:
    """Return the Tier label for a known int value, or the raw number for unknowns."""
    try:
        return Tier(value).name
    except ValueError:
        return str(value)
