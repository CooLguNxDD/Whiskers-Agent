"""
Jules plugin initialization.
"""
import logging as _logging
from plugins.jules_plugin.plugin_config import JulesPlugin

_logger = _logging.getLogger("whiskers.plugins")

def register(registry):
    """
    Registers the Jules plugin.
    """
    try:
        registry.lifecycle.register_plugin(JulesPlugin())
        _logger.info("Jules plugin registered successfully.")
    except Exception as e:
        _logger.error(f"Error during Jules plugin registration: {e}", exc_info=True)
        raise

__all__ = []
