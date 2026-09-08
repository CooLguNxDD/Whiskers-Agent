"""Patchable dependency surface for plugin route handlers.

Each handler submodule (``registry.py`` / ``tools.py`` / ``auth_logs.py`` /
``skills_config.py``) does ``from api.plugin_routes.deps import X``, which
binds its OWN copy of ``X`` at import time. Because of this, patching only
takes effect at the exact binding a handler reads from:

- ``monkeypatch.setattr("api.plugin_routes.<submodule>.X", ...)`` — the
  supported pattern; patches the name the handler actually calls. See
  ``test_plugin_routes_stale.py``.
- ``monkeypatch.setattr("api.plugin_routes.helpers._db_registry.<method>", ...)``
  — patches a method on the single shared ``DBPluginRegistry`` instance
  instead of replacing the object; every submodule's binding points at the
  same instance, so this always propagates regardless of which submodule
  reads it.

``api.plugin_routes.X`` (the package-level re-export in ``__init__.py``) is
**not** a live patch target — it is a one-time snapshot copied at package-init
time for external/back-compat imports, and setting it does not affect any
handler submodule's already-bound name.
"""

from __future__ import annotations

from core.context import mcp, oauth_relay, vault, tool_visibility
from db_layer.connection import get_async_session
from core.plugin_loader.plugin_registry import get_registry
from core.plugin_loader import plugin_loader as _plugin_loader
from core.plugin_loader.skill_file_store import load_skill_from_disk
from api.plugin_routes.helpers import (
    _db_registry,
    _refresh_live_plugin_skills,
    _skill_row_for,
    parse_tier,
    _build_plugin_payload,
)

__all__ = [
    "mcp",
    "oauth_relay",
    "vault",
    "tool_visibility",
    "get_async_session",
    "get_registry",
    "_plugin_loader",
    "load_skill_from_disk",
    "_db_registry",
    "_refresh_live_plugin_skills",
    "_skill_row_for",
    "parse_tier",
    "_build_plugin_payload",
]
