"""Loads the core-owned ``core_roles.json`` bundle into the model-role registry.

Every built-in ladder ships as a single ``[{"selector": "core"}]`` rung, so
loading this module changes nothing about runtime model selection until an
operator (via the DB override store, §7) or a future core JSON edit (S9)
adds real rungs. Also exposes the default ``effort_map`` used by
``resolver.compile_selector`` to turn ``effort:<level>`` selectors into
aliases — see ``core_graph.model_roles.role_spec`` for the vocabulary.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core_graph.model_roles.role_spec import _ALIASES, ModelRoleSpecError, parse_model_role_bundle

logger = logging.getLogger("whiskers.core_graph.model_roles.builtin")

_CORE_ROLES_PATH = Path(__file__).parent / "defaults" / "core_roles.json"
_CORE_OWNER = "core"

_DEFAULT_EFFORT_MAP = {
    "low": "fast",
    "medium": "balanced",
    "high": "strongest",
    "max": "strongest",
}


def _load_raw() -> dict:
    with _CORE_ROLES_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_builtin_roles() -> None:
    """Parse + register every role in ``core_roles.json``. Raises loudly on a bad bundle.

    A typo in the shipped core spec must fail CI/boot, not silently drop a
    role — this is called once, lazily, from
    ``registry._seed_builtin_roles`` via ``registry._ensure_registry()`` (not
    the public ``get_model_role_registry()``, which would re-enter seeding).
    """
    from core_graph.model_roles.registry import _ensure_registry

    data = _load_raw()
    specs = parse_model_role_bundle(data, owner=_CORE_OWNER)
    reg = _ensure_registry()
    for spec in specs:
        reg.register(spec)
    logger.info("model_roles: loaded %d core role(s)", len(specs))


def get_default_effort_map() -> dict[str, str]:
    """Core-shipped ``effort:<level> -> alias`` table (overridable via DB, §7)."""
    try:
        data = _load_raw()
    except Exception:
        logger.warning("model_roles: falling back to hardcoded effort_map", exc_info=True)
        return dict(_DEFAULT_EFFORT_MAP)

    raw = data.get("effort_map")
    if not isinstance(raw, dict):
        return dict(_DEFAULT_EFFORT_MAP)

    out: dict[str, str] = {}
    for level, alias in raw.items():
        if not isinstance(level, str) or not isinstance(alias, str) or alias not in _ALIASES:
            raise ModelRoleSpecError(
                f"core_roles.json effort_map: bad entry {level!r} -> {alias!r}"
            )
        out[level] = alias
    return out
