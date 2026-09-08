"""Registry for plugin-manifest-declared specialist effort (§8.3 of model-role-specs).

Plugins declare a domain-wide default (and optional per-``goal_class``
overrides) in ``manifest.json``:

```jsonc
"settings": {
  "specialist_agent": {
    "effort": "high",
    "effort_overrides": {"discover": "low", "bake_for_job": "max"}
  }
}
```

Loaded by ``core.plugin_loader.lifecycle_manager.LifecycleManager`` (mirrors
the ``flow_specs`` loading site) into this process-wide registry, keyed by
plugin_id, and consulted by ``flow_runner``/``specialist_entry`` as rungs 4-5
of ``core_graph.model_roles.selection.select_agent_model``'s precedence
chain. Unknown effort levels are rejected loudly at load time — never
silently defaulted (fail-closed, matching flow_spec/role_spec parsing).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("whiskers.core_graph.model_roles.manifest_effort")

_VALID_EFFORTS = frozenset({"low", "medium", "high", "max"})


class ManifestEffortError(ValueError):
    """Raised when a manifest's specialist_agent effort config fails validation."""


@dataclass(frozen=True)
class ManifestEffort:
    """A plugin's declared specialist effort tier and per-``goal_class`` overrides,
    as parsed from ``manifest.json``'s ``settings.specialist_agent`` block."""

    plugin_id: str
    effort: str | None = None
    overrides: dict[str, str] = field(default_factory=dict)


def parse_specialist_effort(sa: dict | None, *, plugin_id: str) -> ManifestEffort:
    """Validate ``settings.specialist_agent``'s ``effort``/``effort_overrides`` keys.

    Raises ``ManifestEffortError`` on an unknown effort level — this must
    fail loudly rather than silently default, since a typo'd level would
    otherwise silently route a whole domain onto the wrong model tier.
    """
    if not isinstance(sa, dict):
        return ManifestEffort(plugin_id=plugin_id)

    effort = sa.get("effort")
    if effort is not None and effort not in _VALID_EFFORTS:
        raise ManifestEffortError(
            f"{plugin_id}: specialist_agent.effort must be one of {sorted(_VALID_EFFORTS)}, got {effort!r}"
        )

    raw_overrides = sa.get("effort_overrides") or {}
    if not isinstance(raw_overrides, dict):
        raise ManifestEffortError(f"{plugin_id}: specialist_agent.effort_overrides must be an object")
    overrides: dict[str, str] = {}
    for goal_class, level in raw_overrides.items():
        if not isinstance(goal_class, str) or not isinstance(level, str) or level not in _VALID_EFFORTS:
            raise ManifestEffortError(
                f"{plugin_id}: specialist_agent.effort_overrides has a bad entry "
                f"{goal_class!r}: {level!r} (must map to one of {sorted(_VALID_EFFORTS)})"
            )
        overrides[goal_class] = level

    return ManifestEffort(plugin_id=plugin_id, effort=effort, overrides=overrides)


_registry: dict[str, ManifestEffort] = {}


def register_manifest_effort(entry: ManifestEffort) -> None:
    """Register (or replace) a plugin's specialist effort config."""
    _registry[entry.plugin_id] = entry


def get_manifest_effort(plugin_id: str) -> ManifestEffort | None:
    """Look up a plugin's specialist effort config, or None if never declared."""
    return _registry.get(plugin_id)


def unregister_manifest_effort(plugin_id: str) -> bool:
    """Remove a plugin's specialist effort config (plugin unload/hot-swap)."""
    return _registry.pop(plugin_id, None) is not None


def _reset_manifest_effort_for_tests() -> None:
    """Clear the registry (tests only)."""
    _registry.clear()
