"""
plugin_ids — shared normalization helpers for plugin/operation identifiers.

Centralizes the two string-manipulation patterns that were previously
duplicated across api/tool_routes.py, api/route_routes.py, and
db_layer/permission_store.py.
"""

from __future__ import annotations


def canonical_plugin_id(plugin_id: str) -> str:
    """Normalize a plugin_id to the stored short name.

    The database stores only the short/leaf name (e.g., ``portfolio_plugin``).
    Full dotted package paths (e.g. ``plugins.portfolio_plugin``) are collapsed
    to their last component.
    """
    if not plugin_id:
        return plugin_id
    if "." in plugin_id:
        return plugin_id.rsplit(".", 1)[-1]
    return plugin_id


def strip_operation_prefix(plugin_id: str, operation_id: str) -> str:
    """Strip the ``<plugin_id>__`` prefix from an operation_id if present.

    Accepts either a qualified name (``plugin__op``) or a plain op name and
    always returns the bare op name for DB lookups.
    """
    op = str(operation_id)
    prefix = f"{plugin_id}__"
    if op.startswith(prefix):
        return op[len(prefix):]
    return op
