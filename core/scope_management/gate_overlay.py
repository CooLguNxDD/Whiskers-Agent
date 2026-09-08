"""Load DB-stored plugin-gate overrides (db_layer.plugin_gate_store) into the
in-memory ``PluginGateRegistry``.

Lenient on read: a stored gate that no longer parses (schema tightened since
saved, or hand-edited bad JSON) is logged and skipped rather than raised — a
single bad row must never brick plugin dispatch. Strict validation happens
once, at write time, in ``db_layer.plugin_gate_store``.

Called from two places only, mirroring ``core_graph.model_roles.db_overlay``:
- ``core.bootstrap.phases::phase_6_registry_sync`` (once, at boot) so DB
  overrides survive a restart.
- ``api/config_routes.py``'s plugin-gate routes, right after a successful
  write, so the change is visible to the very next request rather than
  waiting on any cache TTL.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("whiskers.scope_management.gate_overlay")


async def apply_db_overrides() -> int:
    """Reload every DB gate override into the registry. Never raises.

    Returns the number of gate overrides successfully applied.
    """
    from core.scope_management.gates import GateSpecError, get_plugin_gate_registry, parse_gate_spec

    try:
        from db_layer.plugin_gate_store import get_plugin_gate_overrides

        stored = await get_plugin_gate_overrides()
    except Exception:
        logger.warning("gate_overlay: failed to read overrides; leaving registry untouched", exc_info=True)
        return 0

    registry = get_plugin_gate_registry()
    registry.clear_db_overrides()

    applied = 0
    for plugin_id, raw_spec in (stored or {}).items():
        try:
            spec = parse_gate_spec(plugin_id, raw_spec, owner="db")
        except GateSpecError:
            logger.warning("gate_overlay: stored gate for '%s' no longer parses; skipping", plugin_id, exc_info=True)
            continue
        registry.set_db_override(spec.plugin_id, spec)
        applied += 1

    logger.info("gate_overlay: applied %d plugin gate override(s)", applied)
    return applied


def clear_db_overrides_local() -> None:
    """Drop DB overrides from the in-memory registry without touching storage."""
    from core.scope_management.gates import get_plugin_gate_registry

    get_plugin_gate_registry().clear_db_overrides()
