"""
Jules plugin configuration.
"""
import json
from pathlib import Path
from core.dynamic_tools import DynamicPlugin

_manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))

class JulesPlugin(DynamicPlugin):
    """
    Jules plugin main class.
    """
    name = _manifest.get("name", "jules_plugin")
    version = _manifest.get("version", "1.0.0")
    tier = _manifest.get("tier", "pro")
    _package_dir = Path(__file__).parent
    # Server-side fleet tools (roles optional) — import registers @mcp.tool handlers
    module_paths = ["plugins.jules_plugin.MCPTools"]

    async def on_load(self, ctx):
        """Initialize the plugin and register the custom API key auth provider."""
        # Ensure DynamicPlugin's on_load is called (loads OpenAPI tools + module_paths)
        await super().on_load(ctx)
        
        # Jules uses a static API key passed in the 'x-goog-api-key' header.
        # Register a custom auth provider to bypass the direct-login POST pipeline completely.
        async def api_key_auth_provider() -> dict[str, str]:
            """Retrieve the JULES_API_KEY from the vault and return auth headers.

            Returns {} when no key is configured, rather than a header carrying an
            empty value — an empty header still "succeeds" through this provider and
            sends a doomed request upstream instead of surfacing the missing credential.
            """
            from core.context import vault
            api_key = ""
            if vault is not None:
                api_key = await vault.get(self.name, "JULES_API_KEY") or ""
            if not api_key:
                return {}
            return {"x-goog-api-key": api_key}
            
        ctx.register_auth(api_key_auth_provider)
