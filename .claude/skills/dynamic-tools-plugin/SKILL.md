# Dynamic Tools Plugin Skill

A template and guide for generating a new OpenAPI-backed dynamic plugin using `DynamicPlugin` in the Whiskers Agent server.

## When to Use
Use this skill when you have an OpenAPI 3.0 spec (or a set of `mcp-tools.json` files) and want to expose them as live MCP tools without writing any custom Python tool code.

## Minimal Plugin Layout
To create a dynamic tools plugin, structure your plugin directory as follows:
```text
plugins/your_plugin/
  ├── manifest.json         ← Metadata and authentication configuration
  ├── config.json           ← Plugin-specific configuration properties
  ├── plugin_config.py      ← Stripped-down class extending DynamicPlugin
  ├── __init__.py           ← Package entrypoint registering the plugin
  └── src/
      └── mcp-tools-context/← Folder tree containing the tool definitions
```

### 1. `manifest.json` Required Fields
```json
{
  "name": "your_plugin",
  "version": "1.0.0",
  "description": "Your OpenAPI-backed dynamic plugin description.",
  "tier": "pro",
  "config": "config.json",
  "api_url": "https://api.yourdomain.com",
  "layer2_oauth_enabled": true,
  "external_oauth": {
    "external_api": {
      "authorize_url": "http://client.yourdomain.com/oauth-login",
      "token_url": "https://api.yourdomain.com/api/v1/oauth/token",
      "client_id": "your_client_id",
      "scopes": ["ALL"],
      "pkce": "S256-hex",
      "redirect_path": "/oauth/plugin/external_api/callback",
      "login_path": "/api/v1/login",
      "auth_header": "authentication"
    }
  }
}
```

### 2. `config.json` Required Fields
```json
{
  "project_id": 1,
  "scope_id": 1,
  "name": "your_plugin",
  "version": "1.0.0",
  "tool_prefix": "yourprefix",
  "tools_dir": "src/mcp-tools-context",
  "valid_ops": ["read", "write_update", "delete"]
}
```

### 3. `plugin_config.py` Template
```python
import json
from pathlib import Path
from core.dynamic_tools import DynamicPlugin

_manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))

class YourPlugin(DynamicPlugin):
    name = _manifest.get("name", "your_plugin")
    version = _manifest.get("version", "1.0.0")
    tier = _manifest.get("tier", "pro")
    _package_dir = Path(__file__).parent
```

#### 3.1 Static API Key Auth Customization
If your dynamic tools plugin uses a static API key (e.g. `JULES_API_KEY`) passed directly in a header without using a short-lived session token login API:
```python
    async def on_load(self, ctx):
        await super().on_load(ctx)
        
        # Override the auth provider to directly return the API key header
        # from the Vault, bypassing the direct-login POST pipeline completely.
        async def api_key_auth_provider() -> dict[str, str]:
            from core.context import vault
            api_key = ""
            if vault is not None:
                api_key = await vault.get(self.name, "JULES_API_KEY") or ""
            return {"x-goog-api-key": api_key}
            
        ctx.register_auth(api_key_auth_provider)
```

### 4. `__init__.py` Template
```python
import logging as _logging
from plugins.your_plugin.plugin_config import YourPlugin

_logger = _logging.getLogger("whiskers.plugins")

def register(registry):
    try:
        registry.lifecycle.register_plugin(YourPlugin())
        _logger.info("Your plugin registered successfully.")
    except Exception as e:
        _logger.error(f"Error during Your plugin registration: {e}", exc_info=True)
        raise

__all__ = []
```

### 5. `mcp-tools-context/` Layout
Inside the directory configured by `tools_dir` (e.g., `src/mcp-tools-context`), lay out directories by domain name, containing subdirectory operations:
```text
mcp-tools-context/
  └── resource-management/
      ├── read/
      │   └── mcp-tools.json
      ├── write_update/
      │   └── mcp-tools.json
      └── delete/
          └── mcp-tools.json
```

### 6. `mcp-tools.json` Entry Shape
Each `mcp-tools.json` file contains a JSON array of tool definitions:
```json
[
  {
    "name": "getListRecordsPaginated",
    "description": "GET /api/v1/... — Human readable summary",
    "inputSchema": {
      "type": "object",
      "properties": {
        "projectId": {"type": "integer"},
        "pageSize": {"type": "integer"}
      },
      "required": ["projectId"]
    },
    "_meta": {
      "method": "GET",
      "path": "/api/v1/projects/:projectId/records/paginated",
      "domain": "resource-management"
    }
  }
]
```

## Environment Filters
Exposed tools can be filtered at startup using these environment variables:
* **`PLUGIN_TOOL_DOMAINS`**: Comma-separated domain directory names to include (default: all). E.g., `resource-management`.
* **`PLUGIN_TOOL_OPS`**: Comma-separated operations to include (default: all). E.g., `read,write_update`.

## Enabling the Plugin
Add the plugin package path (e.g., `plugins.your_plugin`) to `plugin_config.json` under the `"plugins"` array:
```json
{
  "tier": "pro",
  "plugins": [
    "plugins.core_mcp_plugin",
    "plugins.pro_plugin",
    "plugins.your_plugin"
  ]
}
```

## Verification Steps
1. Run `docker compose up -d` to boot the server.
2. Check the logs for the dynamic tools count (e.g. `dynamic_tools_loader: registered N tools for plugin your_plugin`).
3. Query the MCP client using `list_tools` to confirm the newly registered tools are exposed.

## UI Category Grouping
Dynamic tools can declare tags or categories to enable the collapsible grouping layout in the admin dashboard:
- **`tags`**: Provide a tag (e.g. `["resources"]`) in your route descriptors.
- **`meta.category`**: Or expose category metadata (e.g. `meta={"category": "messages"}`).
The backend exposes this metadata via `group` in the `/api/plugins/{plugin_id}/tools` endpoint. The frontend uses this to organize tools into expandable categories (e.g. `resources`, `messages`), subdivided by `read` or `write` access, with bulk-apply toggles. If none is declared, the tool defaults to the `"general"` category.

## Proxy Tool Custom Description
Upstream **proxy** servers (federated MCP servers added via `/api/proxies`) build their `RouteDescriptor`s in `core/proxy_tools/proxy_tool_loader.py::collect_from_proxy`. A per-proxy `custom_description` (column on `ProxyServer`) is **prepended** to every proxy tool's `description` there, so operator-supplied gateway context flows into both route embeddings and planner candidates. It is threaded via the `proxy.tools_discovered` event payload (`custom_description` key); set it at add time (`POST /api/proxies` body `customDescription`) or edit later with `PATCH /api/proxies/{name}` (re-embeds mounted proxies). The `description` is part of `RouteDescriptor.content_hash`, so a changed value auto-triggers re-embedding on reindex.

