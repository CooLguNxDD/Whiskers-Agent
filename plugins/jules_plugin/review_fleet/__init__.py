"""Jules multi-role code-review fleet — **server-side** templates + config builder.

Canonical home for the prompts used by:

- MCP tools ``julesbuild_review_fleet`` / ``julesfire_review_fleet`` (roles **optional**)
- Plugin skill ``skills/jules-sessions/SKILL.md`` (GOAP planner guidance)
- Optional CLI ``python -m plugins.jules_plugin.review_fleet`` on the server host

Roles default to **none** (no automatic 2+2+1 fleet). Pass an explicit list or
``all`` to build/fire sessions. Keep templates here so MCP tools and GOAP skill
never drift.
"""
from plugins.jules_plugin.review_fleet.templates import (
    ALL_ROLES,
    BASE_TEMPLATE,
    DOC_TEMPLATE,
    FLEET_ALL,
    build_configs,
    build_roles,
    mode_text,
    parse_roles,
)
from plugins.jules_plugin.review_fleet.paths import PLUGIN_SKILL_RELPATH, plugin_skill_path

__all__ = [
    "ALL_ROLES",
    "BASE_TEMPLATE",
    "DOC_TEMPLATE",
    "FLEET_ALL",
    "PLUGIN_SKILL_RELPATH",
    "build_configs",
    "build_roles",
    "mode_text",
    "parse_roles",
    "plugin_skill_path",
]
