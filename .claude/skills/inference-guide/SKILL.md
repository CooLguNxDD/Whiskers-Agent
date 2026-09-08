---
name: inference-guide
description: '**CODE PATTERN SKILL** — Operation contract layer + live inference catalog for Whiskers Agent. USE FOR: OperationDescriptor design, OperationCatalog publish/filter, host HTTP mirroring, catalog REST (list/openapi/execute), createCatalogClient / callCatalogOp / SchemaForm, migrating console API modules off hardcoded paths. DO NOT USE FOR: GOAP embeddings search (discover_tools), FastAPI migration, MCP protocol client (mcpClient.ts).'
argument-hint: 'Optional: plugin_id, operation_id, or UI slot name'
---

# Inference Guide — Contract Layer & Live Catalog

## When to use this skill

- Expanding plugins with new tools/ops that should appear in the console without new hand-written FE API files
- Catalog discovery, execute-by-identity, OpenAPI export for HTTP exposures
- Schema-driven forms (`SchemaForm`, `x-whiskers-ui`)
- Migrating or adding console REST clients via `catalogClient.ts`'s typed namespaces (over `callCatalogOp(owner, name, args)`)
- Understanding `(plugin_id, operation_id)` as the true key

## Mental model

```text
Plugin loaders → RouteDescriptor → RouteRegistry.contribute
  → to_operation() → OperationCatalog (merged with host ops)

@http_route_registry.route(name=…, owner=…)
  → host_catalog.upsert → OperationCatalog (HttpExposure)

Console:
  GET  /api/catalog/session_gated           → filtered ops + revision/ETag
  GET  /api/catalog/session_gated/openapi   → OpenAPI 3 for HTTP exposures
  POST /api/catalog/session_gated/execute   → validate + fast-path invoke

Frontend:
  AppShell useCatalogQuery → setCatalogSnapshot
  src/api/*.ts → catalogClient.<ns>.<method>(args) → callCatalogOp(owner, routeName, args)
  createCatalogClient → resolveHttpCall (path params) or execute POST
  SchemaForm / CatalogActionsPanel for plugin schema-driven UI
```

**Do not** treat `route_embeddings` / `discover_tools` as the live catalog — embeddings are eventually consistent search ranks for GOAP. Live truth is `OperationCatalog` in memory.

Identity key is always **`(plugin_id, operation_id)`**. For host console routes, `plugin_id` is the route **owner** (e.g. `api.plugins`) and `operation_id` is the route **name** (e.g. `api_list_plugins`).

| Concern | Type |
|---|---|
| What can run | `OperationDescriptor` |
| How console REST reaches it | `HttpExposure?` |
| How MCP/agents reach it | `McpExposure?` |
| How console renders it | `UiContribution?` |

---

## Backend modules

| Module | Role |
|---|---|
| `core/route_registry/operation_descriptor.py` | Contract types + validate |
| `core/route_registry/operation_catalog.py` | Live store, filter, revision/ETag |
| `core/route_registry/host_catalog.py` | Mirror `@http_route_registry.route` → catalog; merge with plugin ops |
| `core/route_registry/route_descriptor.py` | Compat façade + `to_operation()` |
| `core/route_registry/route_registry.py` | Plugin contribute → `publish_owner_merged` |
| `core/route_registry/http_route_registry.py` | Declares host routes; calls `upsert_host_declaration` |
| `core/route_registry/execute.py` | Fast-path execute plane. `execute_operation` pops an optional `_response_shape` key from `args` before schema validation and threads it through `utils.api_utils.apply_response_shape` after invoke (v1: opt-in only — omit it and the result is byte-identical raw output, so existing GOAP/FlowSpec callers are unaffected). Skips the pop when the op's own `input_schema` already declares `_response_shape` (no double-shaping). Gated by the same live `graph.direct_call_shaping` kill switch and `graph.direct_call_shaping_exclude` list as `core.context.response_shape_middleware`, the equivalent MCP direct-call path. `OperationDescriptor.to_catalog_dict()` advertises the `_response_shape` property on fast-path ops' `input_schema` (display-only — never affects the schema used for validation or `descriptor_hash`); text sourced from `utils/response_shape_hints.py`. |
| `core/route_registry/openapi_export.py` | OpenAPI 3 from HTTP exposures |
| `api/catalog_routes.py` | Session-gated catalog REST |

Dependency: `jsonschema>=4.20.0`.

### Host route → catalog rules

- Only **named** HTTP handlers (`name=` required) with a callable endpoint.
- Skips `kind=ws`, `owner=__policy__`, empty name, paths not starting with `/`.
- One op per method; identity `(owner, name)`.
- Path `{params}` become required string properties on `input_schema`.
- Empty `required_scopes` (session middleware already gates); public auth → `visibility=public_catalog`.
- **Merge**: host HTTP and plugin tool ops for the same `plugin_id` are merged so neither publish wipes the other (e.g. `cat_terminal_relay_plugin`).

When adding a new host REST endpoint:

```python
@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/widget",
    methods=["GET"],
    name="api_get_plugin_widget",   # → operation_id
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.plugins",              # → plugin_id in catalog
)
async def get_plugin_widget(request: Request) -> Response:
    ...
```

Then FE:

```ts
return callCatalogOp("api.plugins", "api_get_plugin_widget", { plugin_id: id })
```

---

## Frontend patterns

| File | Role |
|---|---|
| `src/api/catalog.ts` | `getCatalog`, `executeOperation`, `getCatalogOpenApi` |
| `src/api/catalogRuntime.ts` | Module-level snapshot + **`callCatalogOp`** / `resolveCatalogPath` |
| `src/api/catalogClient.ts` | **Hand-authored typed namespaces** over `callCatalogOp` (`catalogClient.plugins.enable(id)`, …) — the sanctioned entrypoint for every `src/api/*.ts` module; see its module docstring for the `api.route`/`api_list_routes` dual-response-shape example. No codegen source exists for operation types, so this is authored and kept in sync by hand. |
| `src/api/inferenceClient.ts` | `resolveHttpCall`, `invokeCatalogOperation`, path param fill |
| `src/api/generated/createCatalogClient.ts` | `createCatalogClient` + `client.op` (lower-level factory `catalogClient.ts`/`catalogRuntime.ts` build on). Snapshot miss peeks the live module catalog then refresh-once — same path for `call` and `op`; never a blind `/execute` POST. |
| `src/hooks/useCatalog.ts` | Query + seed `setCatalogSnapshot` (unfiltered 200 only). 304 reuses this query key's cache or throws — never `getCatalogOperations()` into a filtered query. |
| `src/components/shell/AppShell.tsx` | Prefetches full catalog on shell mount |
| `src/components/inference/SchemaForm.tsx` | JSON Schema form + async options |
| `src/components/inference/CatalogActionsPanel.tsx` | Plugin detail actions tab |
| `src/components/playground/ToolTestMode.tsx` | Playground rail stays visible; SchemaForm + `catalogClient.call` is the primary execute path (`resolveCatalogOp`); playground invoke is fallback when the catalog has no match. Never hardcode a tool count. |

### Console module style (session-gated)

`src/api/*.ts` modules call through `catalogClient`, never `callCatalogOp` directly —
`src/api/__tests__/catalogClientBoundary.test.ts` fails CI on a direct import. Add the typed
method to the matching namespace in `catalogClient.ts` first, then call it:

```ts
// catalogClient.ts
plugins: {
  enable: (pluginId: string): Promise<Plugin> =>
    callCatalogOp("api.plugins", "api_enable_plugin", { plugin_id: pluginId }),
  // ...
}

// api/plugins.ts
import { catalogClient } from "./catalogClient"

export function enablePlugin(id: string): Promise<Plugin> {
  return catalogClient.plugins.enable(id)
}
```

Path params must match template names (`plugin_id`, `tool_name`, `entry_id`, …). Leftover args become **query** (GET) or **JSON body** (POST/PUT/PATCH/DELETE).

### SSE / raw streams

Resolve path then fetch:

```ts
const resolved = await resolveCatalogPath("api.playground", "api_playground_stream_goap")
const path = resolved?.path ?? "/api/playground/session_gated/stream_goap"
// stream fetch(path, …)
```

### Still hardcoded (intentionally)

| Module | Why |
|---|---|
| `src/api/admin.ts` | Public pre-auth (`/api/admin/public/*`); catalog is session-gated |
| `src/api/client.ts` | Transport (cookies, 401 refresh) |
| `src/api/catalog.ts` | Bootstrap of the catalog itself |
| `src/api/mcpClient.ts` | MCP protocol, not REST catalog |

### Preferred plugin action UI

```ts
const { client } = useCatalogClient({ pluginId })
const run = client.op(pluginId, `${pluginId}__do_thing`)
await run({ name: "x" })
```

---

## Catalog HTTP API

Path grammar: `/api/catalog/{gate}/…` with `AuthPolicy.SESSION_GATED`.

### GET `/api/catalog/session_gated`

Query: optional `plugin_id`, `slot`. Response: `{ revision, etag, operations }`.  
`If-None-Match` → **304** only when the ETag names **this** filtered view (caller scopes + `plugin_id` + `slot`), never the global `catalog.etag`. `Cache-Control: no-store` on both 200 and 304. A 304 must not be coerced into an empty or unfiltered catalog snapshot on the console (`useCatalogQuery` reuses *that query's* prior data or throws).

### GET `/api/catalog/session_gated/openapi`

HTTP-exposed ops only (paths starting with `/`).

### POST `/api/catalog/session_gated/execute`

```json
{ "plugin_id": "…", "operation_id": "…", "args": { } }
```

Errors: `400` identity, `403` scopes, `404` unknown, `422` schema, `501` non-fast-path.  
Console host CRUD prefers **HttpExposure direct HTTP** (not execute) so Starlette handlers run as today.

---

## Lifecycle rules

1. Host cleanup always `remove_owner` even if `on_unload` fails.
2. Atomic `publish_owner` after validate; merge host+plugin via `publish_owner_merged`.
3. Reinitialize: unload + host cleanup, then `on_load`.

---

## Tests

```bash
docker exec -e PYTHONPATH=/app whiskers-mcp-server pytest \
  test/unit/test_operation_descriptor.py \
  test/unit/test_operation_catalog.py \
  test/unit/test_catalog_routes.py \
  test/unit/test_catalog_execute.py \
  test/unit/test_openapi_export.py \
  test/unit/test_host_catalog.py \
  test/unit/test_host_cleanup_lifecycle.py -q
```

FE:

```bash
npm --prefix frontend/cat-admin-frontend run test:unit -- \
  src/api/__tests__/catalog.test.ts \
  src/api/__tests__/catalogRuntime.test.ts \
  src/api/__tests__/createCatalogClient.test.ts \
  src/api/__tests__/inferenceClient.test.ts \
  src/api/__tests__/apiKeys.test.ts \
  src/api/__tests__/catalogClientBoundary.test.ts \
  src/hooks/__tests__/useCatalog.test.ts \
  src/components/inference/__tests__/SchemaForm.test.tsx
```

Goals: `goals/contract-layer/`.

---

## Anti-patterns

| Don't | Do instead |
|---|---|
| Hardcode `/api/.../session_gated/...` in new FE modules | Add a method to `catalogClient.ts`, call it from `api/*.ts` |
| Call `callCatalogOp(owner, name, args)` directly from an `api/*.ts` module | Add a typed method to `catalogClient.ts`'s matching namespace instead (`callCatalogOp` itself belongs only inside `catalogClient.ts`) |
| Forget `name=` on new `@http_route_registry.route` | Named routes are required for catalog identity |
| Execute with only `operation_id` | Always pass `plugin_id` / owner |
| Use embeddings as exact catalog | `GET /api/catalog/session_gated` |
| Wipe owner on host or plugin publish | `publish_owner_merged` |
| Put React in plugins | Descriptors + host renderers |
| Migrate login/signup to session catalog | Keep public admin paths hardcoded |

---

## Related skills

- `http-route-registry` — path grammar, AuthPolicy, mount/unmount
- `plugin-system` — contribute_routes, lifecycle
- `react-app-guide` — TanStack Query / console architecture
- `openapi-pipeline` — **backend** OpenAPI → MCP tools (live `/openapi.json` or FastAPI/Flask/Express source; different from catalog OpenAPI export)
