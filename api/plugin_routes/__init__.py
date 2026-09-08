"""Plugin management REST API package.

Split from the former monolithic ``api/plugin_routes.py``:
registry/lifecycle, tools+permissions, config+skills, auth/logs.

Importing this package registers all session-gated routes (side effect).
Back-compat: ``import api.plugin_routes`` and patches on this module still work.
"""
from __future__ import annotations

# Side-effect imports: register HTTP routes.
from api.plugin_routes import auth_logs as auth_logs  # noqa: F401
from api.plugin_routes import registry as registry  # noqa: F401
from api.plugin_routes import skills_config as skills_config  # noqa: F401
from api.plugin_routes import tools as tools  # noqa: F401

# Re-export symbols tests / callers patch or import.
from api.plugin_routes.helpers import (  # noqa: F401
    _db_registry,
    _refresh_live_plugin_skills,
    _skill_row_for,
    parse_tier,
    _build_plugin_payload,
)
from api.plugin_routes.registry import (  # noqa: F401
    list_plugins,
    delete_plugin,
    prune_stale_plugins,
    enable_plugin,
    disable_plugin,
    get_plugin,
    get_plugin_health,
)
from api.plugin_routes.tools import (  # noqa: F401
    get_plugin_tools,
    hide_plugin_tools,
    show_plugin_tools,
)
from api.plugin_routes.skills_config import (  # noqa: F401
    get_plugin_skills,
    put_plugin_skill,
    delete_plugin_skill,
    reload_plugin_skills,
    get_plugin_logs,
    get_plugin_config,
    put_plugin_config,
    delete_plugin_config,
)
from api.plugin_routes.auth_logs import (  # noqa: F401
    revoke_plugin_oauth,
    list_mods_alias,
    enable_mod_alias,
    disable_mod_alias,
    set_plugin_direct_credentials,
    delete_plugin_direct_credentials,
    reindex_plugin,
)

# Also re-export deps tests patch on this module path.
from core.context import mcp, oauth_relay, vault, tool_visibility  # noqa: F401
from db_layer.connection import get_async_session  # noqa: F401
from core.plugin_loader.plugin_registry import get_registry  # noqa: F401
from core.plugin_loader import plugin_loader as _plugin_loader  # noqa: F401
from core.plugin_loader.skill_file_store import load_skill_from_disk  # noqa: F401

__all__ = [
    "_db_registry",
    "parse_tier",
    "list_plugins",
    "get_plugin",
    "enable_plugin",
    "disable_plugin",
    "get_plugin_tools",
    "get_plugin_skills",
    "get_plugin_config",
]
