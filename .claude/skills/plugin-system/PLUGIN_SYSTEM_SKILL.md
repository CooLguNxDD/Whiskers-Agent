# Plugin System Skill

## Key Components

| File / Folder | Purpose |
|---|---|
| `config/plugin_config.json` | Active plugin packages + system tier (`free` \| `pro`) — path via `utils.config_registry.PLUGIN_CONFIG_PATH` |
| `config/server_config.json` | Centralized server operational configuration (including pagination, graph, and skill limits `max_skill_file_chars`, `max_skill_total_chars`, `max_formatted_plugin_skills_chars`) |
| `core/plugin_loader/plugin_loader.py` | `discover_and_load_plugins_async()` — two-pass loader + toposort |
| `core/plugin_loader/plugin_registry.py` | `PluginRegistry`, `Plugin` ABC, `PluginContext`, `EventBus` |
| `core/plugin_loader/types.py` | `IPlugin`/`IPluginContext` (`Protocol`) + `PluginManifest` (`TypedDict`) — no sibling-module imports, so anything in the package can type against these without risking a cycle |
| `plugins/core_mcp_plugin/` | FREE tier — core Whiskers Agent tools + LangGraph flows |
| `plugins/pro_plugin/` | PRO tier — dynamic routing, 300+ endpoints via pgvector |
| `plugins/report_plugin/` | FREE tier — async export pipeline |
| `plugins/test_oauth_plugin/` | FREE tier — Layer 2 ExternalOAuthRelay smoke-test |
| `plugins/job_search_plugin/` | FREE tier — job search, evaluation, and application tracking (settings configured in `manifest.json`, e.g., `apply_endpoints`) |


---

## Boot Sequence

```
whiskers_mcp.py
  └─ discover_and_load_plugins_async()
       ├─ PASS 1: import each package, read+interpolate manifest.json, build toposort
       ├─ Toposort by `requires`
       └─ PASS 2 (sorted):
            ├─ load_plugin_config()
            ├─ Auth delegation wiring (registry.set_auth_delegate + relay.register_delegate)
            ├─ _db_upsert_and_validate() → PluginLoadError → skip plugin, load others
            ├─ package.register(registry)
            └─ submodule loop
  └─ oauth_relay.update_manifests()
  └─ registry.initialize_plugins()
       ├─ plugin.on_load(ctx)   ← Phase 1: register tools, declare services (no I/O)
       └─ plugin.on_ready(ctx)  ← Phase 2: connect DB, warm caches (I/O OK)
  └─ tool_visibility.apply_persisted(await get_disabled_tools())
       ← hide tools marked is_enabled=FALSE in `tool_config` (core_017) from FastMCP.
         Runs after on_ready so every @mcp.tool() has registered; reversible per
         tool via api/tool_routes.py. Independent of route_embeddings.is_enabled.
  └─ if GATEWAY_UNIFIED: tool_visibility.apply_hidden(_collect_gateway_hidden_names())
       ← run_graph unified mode (core_027): hide every tool flagged hidden so
         run_graph is the only visible MCP tool.
```

## Gateway (run_graph unified) mode

Goal: expose only `run_graph` (+ `discover_tools`, auth) to MCP clients while
keeping every underlying tool runnable *inside* run_graph. One platform tool =
unlimited tools behind the scenes — run_graph executes via the route
registry / fast-path, never FastMCP `call_tool`, so hiding a tool from exposure
does not block internal use.

- **Config**: `server_config.json → gateway.run_graph_unified` (`utils.server_config.GATEWAY_UNIFIED`).
- **Hidden ≠ disabled**: `tool_config.is_hidden` (gateway, still internally run-able) vs `tool_config.is_enabled` (fully off). Per-plugin hide = `plugins.meta.tools_hidden=true` → expands to that plugin's `capabilities`.
- **Allowlist**: `tool_visibility.GATEWAY_ALWAYS_VISIBLE` = `{run_graph, discover_tools, authenticate, complete_authentication}` — never hidden.
- **Live toggles**: `POST/DELETE /api/plugins/{id}/tools/{name}/hide` (per-tool), `POST/DELETE /api/plugins/{id}/hide-tools` (per-plugin) → persist + `hide_gateway`/`show_gateway`.
- **Discovery**: `discover_tools(query?, plugin?, limit)` scans `route_embeddings` live each call (hot-reload-safe). `run_graph(mode="discover")` = embedder-only dry run returning ranked candidate tools/params with no execution (confirm-before-execute fallback).
- **Store**: `db_layer/tool_config_store.py` — `get_hidden_tools`, `set_tool_hidden`, `get_tool_hidden_states`.

---

## PluginRegistry API

```python
registry = PluginRegistry(mcp)
registry.elevate_tier(Tier.PRO)
registry.register_plugin(plugin)
await registry.initialize_plugins()
await registry.teardown_plugins()
```

---

## PluginContext (Service Locator)

```python
ctx.register_service("db", db_pool)    # Share service across plugins
ctx.get_service("db")                  # Locate shared service
ctx.register_auth(fn)                  # Register plugin auth provider
ctx.delegate_auth_to("core_mcp_plugin") # Resolve auth via another plugin
ctx.events.on("record.created", fn)  # Subscribe to cross-plugin events
ctx.events.emit("record.created", id) # Publish cross-plugin event
ctx.enable_tools()                     # Sync event → mcp.enable(tags={plugin_id})
ctx.disable_tools(names)               # Sync event → mcp.disable (by name or tag)
ctx.contribute_routes(routes)          # Sync event → route_registry.contribute
ctx.register_binding(binding)          # Sync event → route_registry.register_binding
ctx.remove_routes()                    # Sync event → route_registry.remove_plugin
ctx.get_app()                          # Raw FastMCP instance (last resort)
ctx.config                             # Dict from config/plugin_config.json
ctx.artifact_store                     # IArtifactStore — put_bytes/get_bytes/presigned_url/create_link/...
ctx.auth_service                       # IAuthService — principal_from_bearer/principal_from_session_cookie/mint_scoped_token
```

Both properties are declared on the `IPluginContext` protocol (`core/plugin_loader/types.py`,
typed `Any` to keep that module free of any `core.interfaces` import), not just on the concrete
`PluginContext` — `Plugin.on_load`/`on_ready`/`on_unload` type their `ctx` param as `IPluginContext`,
so a lifecycle hook reaching `ctx.artifact_store`/`ctx.auth_service` type-checks.

`artifact_store`/`auth_service` are lazily-resolved process-wide singletons (same object every call,
not per-plugin) — a plugin must never import `core.artifact_store.minio_client`,
`db_layer.artifact_link_store`, `db_layer.api_key_store`, `core.api_key_management.*`, or reach
`oauth_provider._svc`/`OAuthService._mint_jwt` directly (`test_plugin_core_import_boundary.py` fails
CI on it). Most call sites — route handlers, `@mcp.tool()` functions, the terminal relay's pre-accept
WS handshake — never receive a `ctx` at all; those import `core.artifact_store.get_artifact_store()` /
`core.auth_service.get_auth_service()` directly instead. See skill `scope-management`'s "Plugin-facing
auth boundary" section for the full `IAuthService` surface.

### EventBus lifecycle wiring

`PluginRegistry._wire_events()` subscribes manager handlers on the shared bus:

| Event | Handler | Mode |
|---|---|---|
| `tools.enable` | `app.enable(tags={plugin_id})` | sync `emit` |
| `tools.disable` | `app.disable(names=…)` or `by_tag` | sync `emit` |
| `routes.contribute` | `route_registry.contribute(routes)` | sync `emit` |
| `routes.bind` | `route_registry.register_binding(binding)` | sync `emit` |
| `routes.remove` | `route_registry.remove_plugin(plugin_id)` | sync `emit` |
| `plugin.boot` | `auth._vault_clear_direct_token(plugin_id)` | async `emit_async` |

Default `Plugin.on_load`/`on_unload` use the `ctx.*` facades — plugins never import `core.context` globals for route/tool mutations. Cross-registry boot ordering uses `await events.emit_async("plugin.boot", plugin_id)` from `PluginLifecycleRegistry` before `on_ready`.

Auth-sensitive plugins must define `name`, `version`, `tier` on the `Plugin` subclass so registry keys and delegation targets resolve consistently.

---

## Plugin ABC

```python
from core.plugin_loader.plugin_registry import Plugin, PluginContext

class MyPlugin(Plugin):
    name = "my_plugin"; version = "1.0.0"; tier = "free"

    async def on_load(self, ctx: PluginContext) -> None:
        from plugins.my_plugin import MCPTools  # noqa: F401
        ctx.register_service("my_svc", MyService())

    async def on_ready(self, ctx: PluginContext) -> None:
        await ctx.get_service("my_svc").connect()

    async def on_unload(self, ctx: PluginContext) -> None:
        await ctx.get_service("my_svc").disconnect()
```

---

## Plugin Credentials

### `required_credentials` vs `optional_credentials`

| Field | Load-gated? | TUI surfaced? | Use when |
|---|---|---|---|
| `required_credentials: ["KEY"]` | **Yes** — `PluginLoadError` if missing | Yes | Plugin cannot function without the key |
| `optional_credentials: ["KEY"]` | **No** — informational only | Yes | Either-or keys (e.g. Tavily OR Brave), nice-to-have |

Both are `list[str]` of env-var-style key names. Both flow through `PluginSpec` (`core/plugin_loader/resolver.py`).

### Vault (pgcrypto storage)
Credentials are stored encrypted at rest in the `plugin_credentials` table via `VaultService` (`db_layer/vault.py`).
- Keyed by `(plugin_id, key_name)`.
- API: `vault.set(plugin_id, key, value)` / `vault.get(plugin_id, key)` / `vault.list_keys(plugin_id)` / `vault.missing_keys(plugin_id, required)`.
- In tool code: resolve vault-first with env fallback — see `search_plugin` pattern:
  ```python
  async def _resolve_key(key_name: str) -> str | None:
      try:
          from core.context import vault
          if vault is not None:
              val = await vault.get("my_plugin", key_name)
              if val:
                  return val
      except Exception:
          pass
      return os.environ.get(key_name) or None
  ```
- Via `PluginContext`: `await ctx.get_credential(key_name)` → `vault.get_required(plugin_id, key)`.

### Interactive management
```bash
python terminal/script/manage_credentials.py    # rich TUI — pick plugin → add/update/delete keys
python terminal/script/add_credentials.py -p <plugin_id> --key KEY --value VAL   # one-shot CLI
```

**Docker (recommended on Windows):** container `.env` uses `@postgres:5432`:
```powershell
docker exec -it whiskers-mcp-server python /app/terminal/script/manage_credentials.py
```

**Windows host:** override `DATABASE_URL` to `@localhost:5432` (same user/pass/db); `MASTER_KEY` from `.env`.

Plugin vault keys (e.g. `search_plugin` `TAVILY_API_KEY`) need **no server restart** — tools resolve vault-first at call time. LLM provider keys cached in `_llm_cache` still require a process restart.

See [ReadMe.md](../../../ReadMe.md#plugin-credential-vault) for full TUI flow and restart matrix.

First-run setup (`scripts/setup.py plugins`) auto-seeds `required_credentials` and `optional_credentials` from env vars when present.

---

## Adding a New Plugin — Checklist

```
plugins/my_plugin/
  manifest.json      ← { name, version, tier, description, [requires], [required_credentials], [optional_credentials], ["skills": ["skills/FOO/SKILL.md"]] }
  plugin_config.py   ← local config loader (interpolates manifest ${VAR} values)
  __init__.py        ← register(registry) with side-effect import of MCPTools
  MCPTools/
    __init__.py      ← from . import my_tools  # noqa: F401
    my_tools.py      ← @mcp.tool() decorated functions

## Hot-swap notes (core_028)
- Enabling/disabling a tool at runtime now calls `reinitialize_plugin` so that `on_load` re-applies the disabled filter and re-contributes routes. This fixes hot-swap for tools that were disabled when the server first started.
- **Lazy single-plugin load**: plugins persisted `is_active=false` at boot are excluded from `_plugin_id_map`. `POST /api/plugins/{id}/enable` (or per-tool toggle) calls `reinitialize_plugin` → `load_single_plugin()` imports/registers the package on demand, then runs `on_load`/`on_ready` + `plugin.boot` + auto `enqueue_pending` for route embeddings.
- **Proxy-aware lifecycle**: `proxy_*` plugin ids bypass `_plugin_id_map`; `reinitialize_plugin`/`teardown_plugin` delegate to `ProxyManager.enable_proxy`/`disable_proxy` (mount/unmount without DB row delete). `unmount_proxy` matches FastMCP 3.x `_WrappedProvider._inner`; `disable_proxy` also calls `route_registry.remove_plugin(f"proxy_{name}")`.
- **Capability enable API**: `mcp.enable(names={cap})` / `mcp.disable(names={cap})` — FastMCP keyword selectors, not positional args.
- **Gateway visibility re-hiding**: `on_load` queries `PluginContext.hidden_tool_names()` and calls `tool_visibility.hide_gateway(n)` to preserve tool hiding across hot-swap/reinitialize.
- **Authoritative tools route list**: `get_plugin_tools` (`GET /api/plugins/{id}/tools`) builds the list using `RouteRegistry` routes to retrieve hidden and enabled tools, merging with config states (disabled tools) and capabilities.
- Gateway `run_graph_unified` and per-tool `is_hidden` toggles call `refresh_tools_summary()` to keep MCP instructions up to date.
```

```python
# plugin_config.py
import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("whiskers.plugins")

_manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))
_config_path = Path(__file__).parent / _manifest.get("config", "config.json")
_config = json.loads(_config_path.read_text(encoding="utf-8")) if _config_path.exists() else {}

def _resolve(val):
    if isinstance(val, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), val)
    return val

# Dynamically resolve api_url from manifest (which can use prefix-specific env vars)
API_URL = _resolve(_manifest.get("api_url", "https://api.yourdomain.com"))
PLUGIN_ID = _manifest.get("name", Path(__file__).parent.name)

async def plugin_auth_headers() -> dict:
    from core.plugin_loader.plugin_registry import get_registry
    return await get_registry().auth.get_auth_headers(PLUGIN_ID)
```

```python
# __init__.py
def register(registry):
    from plugins.my_plugin import MCPTools  # noqa: F401
    logger.info("my_plugin tools registered.")
```

```python
# MCPTools/my_tools.py
from core.context import mcp
from plugins.my_plugin.plugin_config import API_URL, plugin_auth_headers
from utils import safe_api_call
import requests

logger = logging.getLogger("whiskers")

@mcp.tool()
async def my_tool(param: str) -> dict:
    if not param:
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["param"]}
    headers = await plugin_auth_headers()
    return safe_api_call(lambda: requests.get(f"{API_URL}/api/v1/something", headers=headers, timeout=30),
                         context="my_tool")
```

Add to `plugin_config.json`:
```json
{ "tier": "free", "plugins": ["plugins.core_mcp_plugin", "plugins.my_plugin"] }
```

**Manifest `skills` field (planner workflow context)**: Optional list of relative markdown paths. Loaded at `resolver.build_specs` time (frontmatter stripped, per-file 1500 / total ~4000 char caps, skills_map for per-file), seeded to DB (plugins.meta.skills) on first load ("initial load") via loader, DB wins on subsequent boots/reinits. `core/plugin_loader/skill_registry` populated with effective (DB-preferred) concat text. CRUD via API + new Skills tab on plugin detail (add/mod/delete). Only injected for candidate plugins via `_format_plugin_skills`. See `plugins/report_plugin/manifest.json` + `skills/export-report/REPORT_SKILL.md`. DB methods in plugin_registry_store preserve user skills across manifest re-register.

**Manifest `poll_specs` field (async wait/poll steps)**: Optional list declaring async-job pipelines so the goal agent can poll-until-ready. Each spec: `{ "after": <create_op>, "poll_op": <status_op>, "before": <consumer_op>, "match": {"id": "$create.id"}, "until": {"field": "status", "equals": "COMPLETED"}, "fail_on": ["FAILED","EXPIRED"], "interval_s": 60, "max_polls": 30 }`. Loaded alongside skills into `skill_registry` (`set_plugin_poll_specs` / `get_all_poll_specs`, cleared on hot-swap). `core_graph/goap/wait_inject.py::inject_wait_steps` reads them at plan time and splices a `kind:"wait"` step (executed by `core_graph/node/wait.py::wait_node`) between the create op and its consumer. First consumer: `plugins/report_plugin/manifest.json` (export create → poll `get_get_export_requests` → download).

**Manifest `scopes` field (plugin-declared permission vocabulary)**: Optional list declaring the plugin's API-key scope tokens — objects `{"token": "group:my_plugin:read", "description": "..."}` or bare-string shorthand. Loaded into `core/plugin_loader/scope_registry.py` (sibling of skill_registry; seeded on load/reinit, cleared on hot-swap). Validation is anti-squatting: a plugin may only declare `plugin:<own-id>` / `group:<own-id>:<tag>` — invalid tokens are logged and skipped. Tokens flow into `core.api_key_management.scopes.get_valid_scopes()` (static `OAUTH_VALID_SCOPES` floor ∪ contributed) and the vocab endpoint's `plugin_scopes` list, so they appear in the API-key ScopeConfigurator UI and are enforceable by `is_allowed` with zero core edits. Living example: `plugins/jules_plugin/manifest.json`.

### Content hash & staleness (version-control signal)

Mirrors the `scopes_hash` compare-and-resync pattern for source-tree / proxy-identity drift:

| Piece | Role |
|-------|------|
| `core/plugin_loader/content_hash.py` | `compute_plugin_tree_hash(package_dir)` (sha256 over sorted `*.py`/`*.md`/`manifest.json`/`config.json`, pruning `__pycache__`/`.git`/`node_modules`); `compute_proxy_hash(identity)` (canonical JSON of name/url/transport/auth_mode/custom_description — **never secrets**); `append_version_history(meta, version, hash, cap=20)` |
| `plugins.content_hash` column | **Source of truth** (migration `core_037`). `DBPluginRegistry.set_content_hash` writes the column + appends `meta.version_history` (cap 20); strips any legacy `meta.content_hash`. |
| `PluginSpec.content_hash` | Set in `resolver.build_specs` (failure → `""`, never skips plugin). Dedupe: skip duplicate `plugin_config.json` package entries and second packages claiming the same normalized manifest name. |
| Loader | Immediately after DB register (even on credential `PluginLoadError`): if stored column ≠ `spec.content_hash` → log + `set_content_hash`. Rides free on `load_single_plugin` / reinitialize. |
| Boot dedupe | `discover_and_load_plugins_async` only: skip specs already in `lifecycle._plugin_id_map`. **Never** apply in `load_single_plugin` (would break hot-swap). |
| `register()` meta splice | Preserves `skills`, `version_history`, `scopes`, `scopes_hash`, `stale`, `stale_since`. Does **not** touch the `content_hash` column. |
| Phase 2b | `phase_plugin_reconcile` (after proxy mount, before scope preseed): ghost rows (not on disk / not in `proxy_servers`) → `mark_stale`; present + flagged → `clear_stale`; writes only on transitions; **does not** flip `is_active`. |
| API / UI | List payload: `content_hash` (column), `stale`. `DELETE /api/plugins/{id}` (SESSION_GATED; 409 if live; 409 if active non-stale; `proxy_*` → `remove_proxy`). `POST /api/plugins/prune-stale`. Frontend PluginCard: destructive Stale badge, disabled enable Switch, Remove confirm → delete mutation. |

Disabled-but-present plugins stay `stale: false` — staleness is only `meta.stale`, never “not loaded”.

**Checklist:**
- [ ] `manifest.json` exists with correct tier
- [ ] `__init__.py` has `register(registry)` 
- [ ] Plugin in `plugin_config.json` after its dependencies
- [ ] Imports absolute from project root (`from core.context import mcp`)
- [ ] Tools contributed to `route_registry` in plugin's `on_load(ctx)` (using `static_tool_loader.collect_from`) so they are accessible by the dynamic graph orchestrator
- [ ] CLAUDE.md "Available Tools" updated

---

## Tier System

`plugin_config.json` `"tier"` sets system tier. Plugins with higher tier than system are skipped. `registry.elevate_tier(Tier.PRO)` called automatically if config has `"tier": "pro"`.

---

## Import Rules

```python
# ✅ Absolute from project root
from core.context import mcp
from plugins.core_mcp_plugin.plugin_config import API_URL
from utils import safe_api_call

# ✅ Relative within same plugin
from .my_helper import some_function

# ❌ Never cross-plugin relative imports
from ..other_plugin.MCPTools import something
```

---

## EventBus (Cross-Plugin Communication)

```python
# Plugin A — publish
ctx.events.emit("appointment.scheduled", appointment_id=42)

# Plugin B — subscribe (in on_load)
async def on_appointment(appointment_id):
    await notify_record(appointment_id)
ctx.events.on("appointment.scheduled", on_appointment)
```
Both sync and async handlers supported. Async handlers wrapped in `asyncio.create_task()`.

---

## Tools Generator

```bash
python Tools/tools_generator.py --config my_config.yaml \
  --output plugins/core_mcp_plugin/MCPTools/new_tools.py --schema
```
`--schema` flag prints the JSON schema definitions for the tools.

---

## Dropped
- "Last Updated" date header
- Prose intro paragraph (boot sequence diagram says it all)
- PluginContext narrative text
- Plugin ABC `min_api_version` (internal implementation detail)
- "Adding a Static-Tools Plugin" full section (duplicate of CLAUDE.md checklist + generated code pattern)
- Import rules prose (already in CLAUDE.md)
- Tools generator narrative
- Legacy `langgraph_flows/tool_schemas.py` references from checklist.

## Related
- `.claude/skills/model-role-specs/` — manifest `settings.specialist_agent.effort`/`effort_overrides`
  and `settings.model_roles` (plugin-contributed role ladders), loaded by
  `PluginLifecycleManager._load_specialist_effort`/`_load_model_roles`.
