"""
Process-level registry for loaded plugin skill markdown text.

Populated at plugin load time from manifest-declared "skills" relative paths.
Text (frontmatter-stripped, size-capped) is injected into GOAP/linear planner
prompts *only* for plugins that appear in the current turn's route candidates.
"""

import logging
from typing import Optional

logger = logging.getLogger("whiskers.plugins")

# short plugin name (from manifest["name"]) -> skill text
_plugin_skills: dict[str, str] = {}
# short plugin name -> list of poll specs
_plugin_poll_specs: dict[str, list[dict]] = {}


def set_plugin_skills(name: str, text: str) -> None:
    """Store (or overwrite) the skill text for a plugin by its short name."""
    if not name:
        return
    _plugin_skills[name] = text or ""


def get_plugin_skills(name: str) -> str:
    """Return stored skill text for plugin ("" if none)."""
    if not name:
        return ""
    return _plugin_skills.get(name, "") or ""


def set_plugin_poll_specs(name: str, specs: list[dict]) -> None:
    """Store (or overwrite) the poll specs for a plugin by its short name."""
    if not name:
        return
    _plugin_poll_specs[name] = list(specs or [])


def get_plugin_poll_specs(name: str) -> list[dict]:
    """Return stored poll specs for plugin ([] if none)."""
    if not name:
        return []
    return _plugin_poll_specs.get(name, []) or []


def get_all_poll_specs() -> list[dict]:
    """Return a flat list of all stored poll specs."""
    result = []
    for specs in _plugin_poll_specs.values():
        result.extend(specs)
    return result


def clear(name: Optional[str] = None) -> None:
    """Clear a specific plugin's skills and poll specs or all if name is None."""
    if name:
        _plugin_skills.pop(name, None)
        _plugin_poll_specs.pop(name, None)
    else:
        _plugin_skills.clear()
        _plugin_poll_specs.clear()


def get_all_skills() -> dict[str, str]:
    """Return a copy of the full skills map (for debug / tests)."""
    return dict(_plugin_skills)
