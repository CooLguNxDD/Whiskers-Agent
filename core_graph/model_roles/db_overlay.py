"""Load DB-stored model-role overrides (db_layer.model_role_store) into the
in-memory registry/resolver.

Lenient on read: a stored role spec that no longer parses (schema tightened
since it was saved, or hand-edited bad JSON) is logged and skipped rather
than raised — a single bad row must never brick the graph. Strict validation
happens once, at write time, in ``db_layer.model_role_store``.

Called from two places only:
- ``core.bootstrap.phases`` (once, at boot) so DB overrides survive a restart.
- the three config routes in ``api/config_routes.py``, right after a
  successful write, so the change is visible to the very next request without
  waiting for ``core_graph.runtime.bootstrap.invalidate_graph()``'s pool
  snapshot TTL. ``invalidate_graph()`` remains the hook that busts the
  compiled-graph cache and the resolver's pool snapshot; this module is the
  one that reloads *which specs* the registry serves.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("whiskers.core_graph.model_roles.db_overlay")


async def apply_db_overrides() -> int:
    """Reload every DB override into the registry + resolver. Never raises.

    Returns the number of role overrides successfully applied (the
    effort_map, if present, is applied regardless and not counted).
    """
    from core_graph.model_roles.registry import get_model_role_registry
    from core_graph.model_roles.resolver import set_effort_map_override
    from core_graph.model_roles.role_spec import ModelRoleSpecError, parse_model_role_spec

    try:
        from db_layer.model_role_store import get_model_role_overrides

        stored = await get_model_role_overrides()
    except Exception:
        logger.warning("model_roles.db_overlay: failed to read overrides; leaving registry untouched", exc_info=True)
        return 0

    registry = get_model_role_registry()
    registry.clear_db_overrides()

    applied = 0
    for role_id, raw_spec in (stored.get("roles") or {}).items():
        try:
            spec = parse_model_role_spec(raw_spec, owner="db")
        except ModelRoleSpecError:
            logger.warning("model_roles.db_overlay: stored spec for '%s' no longer parses; skipping", role_id, exc_info=True)
            continue
        registry.set_db_override(spec.role_id, spec)
        applied += 1

    effort_map = stored.get("effort_map")
    set_effort_map_override(effort_map or None)

    logger.info("model_roles.db_overlay: applied %d role override(s)%s", applied, " + effort_map" if effort_map else "")
    return applied


def clear_db_overrides_local() -> None:
    """Drop DB overrides from the in-memory registry/resolver without touching storage."""
    from core_graph.model_roles.registry import get_model_role_registry
    from core_graph.model_roles.resolver import set_effort_map_override

    get_model_role_registry().clear_db_overrides()
    set_effort_map_override(None)
