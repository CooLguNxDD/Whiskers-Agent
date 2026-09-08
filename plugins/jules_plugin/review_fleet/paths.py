"""Filesystem anchors for the Jules review fleet (plugin skill + package root)."""
from __future__ import annotations

from pathlib import Path

# plugins/jules_plugin/
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent

PLUGIN_SKILL_RELPATH = "skills/jules-sessions/SKILL.md"


def plugin_root() -> Path:
    """Return the jules_plugin package directory."""
    return _PLUGIN_ROOT


def plugin_skill_path() -> Path:
    """Absolute path to the GOAP-facing jules-sessions skill markdown."""
    return _PLUGIN_ROOT / PLUGIN_SKILL_RELPATH


def repo_root() -> Path:
    """Best-effort Whiskers Agent repo root (parent of ``plugins/``)."""
    return _PLUGIN_ROOT.parent.parent
