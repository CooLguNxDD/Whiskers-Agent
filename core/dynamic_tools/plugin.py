"""
core/dynamic_tools/plugin.py

Base class for plugins backed by an mcp-tools-context JSON tree.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from core.plugin_loader.plugin import Plugin
from core.plugin_loader.plugin_context import PluginContext
from core.config_loader import get_plugin_config, get_response_hints
from .loader import load_tools_from_env, unload_tools

logger = logging.getLogger("whiskers.plugins")


def _resolve_env_vars(val: Any) -> Any:
    """Resolve environment variable placeholders like ${VAR} in string values."""
    if isinstance(val, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), val)
    return val


def _build_auth_config(plugin_name: str, manifest: dict, api_url: str) -> dict | None:
    """Build direct auth config dictionary from manifest definitions."""
    external_oauth = manifest.get("external_oauth")
    if not external_oauth:
        return None

    provider = next(iter(external_oauth), "whiskers_core")
    prov_cfg = external_oauth.get(provider, {})
    
    login_path = _resolve_env_vars(prov_cfg.get("login_path", "/api/v1/login"))
    auth_header = _resolve_env_vars(prov_cfg.get("auth_header", "authentication"))
    base_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    
    oauth_val = manifest.get("layer2_oauth_enabled", True)
    if isinstance(oauth_val, str):
        layer2_oauth_enabled = oauth_val.lower() in ("true", "1", "yes", "on")
    else:
        layer2_oauth_enabled = bool(oauth_val)

    async def username_provider() -> str:
        """Asynchronously fetch the plugin username from the vault."""
        from core.context import vault
        if vault is not None:
            val = await vault.get(plugin_name, "username")
            if val:
                return val
        return ""

    async def password_provider() -> str:
        """Asynchronously fetch the plugin password from the vault."""
        from core.context import vault
        if vault is not None:
            val = await vault.get(plugin_name, "password")
            if val:
                return val
        return ""

    return {
        "login_url": f"{api_url}{login_path}",
        "username_provider": username_provider,
        "password_provider": password_provider,
        "auth_header": auth_header,
        "provider": provider,
        "base_headers": base_headers,
        "layer2_oauth_enabled": layer2_oauth_enabled,
    }


class DynamicPlugin(Plugin):
    """Base class for plugins backed by an mcp-tools-context JSON tree.

    Subclass must define: name, version, tier, and _package_dir = Path(__file__).parent.
    Reads tools_dir / valid_ops / tool_prefix / project_id from config.json.
    Reads api_url / external_oauth from manifest.json.
    """
    _package_dir: Path
    module_paths: list[str] = []

    async def on_load(self, ctx: PluginContext) -> None:
        """Parse manifest, build configs, load tools, and execute default Plugin.on_load."""
        manifest_path = self._package_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        api_url = _resolve_env_vars(manifest.get("api_url", ""))
        cfg = get_plugin_config(self.name)

        # Patch instance attributes so super().on_load registers them correctly
        self.auth_config = _build_auth_config(self.name, manifest, api_url)
        
        project_id_raw = _resolve_env_vars(cfg.get("project_id", 1))
        if project_id_raw is None:
            project_id = 1
        else:
            try:
                project_id = int(project_id_raw)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Invalid project_id for plugin {self.name}: {project_id_raw!r}"
                ) from None

        self.instance_config = {
            "PROJECT_ID": project_id,
            "API_URL": api_url,
        }

        # Build auth_fn for tool closures (invokes registry auth at runtime)
        plugin_id = self.name
        async def _auth_fn() -> dict:
            from core.plugin_loader.plugin_registry import get_registry
            return await get_registry().auth.get_auth_headers(plugin_id)

        tools_dir = self._package_dir / cfg.get("tools_dir", "src/mcp-tools-context")
        shape_hint, format_hint = get_response_hints(self.name)

        # Register tools via FastMCP BEFORE super().on_load collects routes
        load_tools_from_env(
            plugin_id=self.name,
            tools_dir=tools_dir,
            api_url=api_url,
            auth_fn=_auth_fn,
            valid_ops=set(cfg.get("valid_ops", ["read", "write_update", "delete"])),
            tool_prefix=cfg.get("tool_prefix", ""),
            shape_hint=shape_hint,
            format_hint=format_hint,
        )

        # super().on_load: wires auth, collects dynamic tools tagged with self.name,
        # registers fast-path RouteDescriptors, enables tag, contributes routes, and binds.
        await super().on_load(ctx)

    async def on_unload(self, ctx: PluginContext) -> None:
        """Disable tools, unload from FastMCP, and run teardown."""
        from core.context import mcp as _mcp
        _mcp.disable(tags={self.name})  # hide before state clear
        unload_tools(self.name)         # remove from FastMCP
        await super().on_unload(ctx)    # remove route contributions & bindings
