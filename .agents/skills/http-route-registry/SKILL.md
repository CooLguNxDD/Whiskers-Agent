---
name: http-route-registry
description: '**CODE PATTERN SKILL** — Unified HTTP/WS Route Registry for Whiskers Agent Server. USE FOR: declaring routes using @http_route_registry.route, managing route auth policies, hot-reloading routes (mount/unmount by owner/path), and debugging route registration issues.'
argument-hint: 'Optional: path to route module or plugin'
---

# HTTP/WS Route Registry

## Overview

The Whiskers Agent Server utilizes a unified `HttpRouteRegistry` singleton to register and manage all HTTP and WebSocket routes. This system resolves mount-timing bugs (where routes declared in plugins were mounted too late to be captured by Starlette) and enables runtime hot-reloading and route-specific authorization policies.

- **Registry Singleton**: `core.route_registry.get_http_route_registry()` (also imported as `http_route_registry` from `core.context`)
- **Route Declaration**: `@http_route_registry.route(...)` decorator replaces `@mcp.custom_route(...)`
- **Auth Policy Gating**: Explicit `AuthPolicy` values are used by `PublicPathMiddleware` and `SessionGateMiddleware` at runtime
- **Path grammar (data-driven)**: console/backend HTTP+WS routes use `/api/{route}/{permission_gate}/{endpoint...}`

### Operation contract layer (inference catalog)

Canonical executable identity is `(plugin_id, operation_id)` via `OperationDescriptor` + live
`OperationCatalog`, catalog REST, and FE `createCatalogClient` / `SchemaForm`.

**Full patterns → skill `inference-guide`** (`.Codex/skills/inference-guide/SKILL.md`).

| Method | Path | Role |
|---|---|---|
| GET | `/api/catalog/session_gated` | Entitlement-filtered ops + ETag/revision |
| GET | `/api/catalog/session_gated/openapi` | OpenAPI 3 slice of ops with HttpExposure |
| POST | `/api/catalog/session_gated/execute` | Schema-validated execute by identity pair |

---

## Path Grammar

```text
/api/{route_name}/{permission_gate}/{endpoint...}
```

`permission_gate` is the `AuthPolicy.value`:

| AuthPolicy | Path segment |
|---|---|
| `PUBLIC` | `public` |
| `SESSION_GATED` | `session_gated` |
| `SCOPE_REQUIRED` | `scope_required` |
| `NONE` | `none` |

Examples:

| Intent | Path |
|---|---|
| Admin login | `/api/admin/public/login` |
| List plugins | `/api/plugins/session_gated` |
| Plugin tool state | `/api/plugins/session_gated/{plugin_id}/tools/{tool_name}/state` |
| Terminal REST | `/api/terminal/session_gated/hosts` |
| Terminal console WS | `/api/terminal/none/ws/{session_id}` |
| Analytics WS | `/api/analytics/none/ws` |
| Portfolio layout | `/api/portfolio/public/layout` |

Helpers (single source of truth):

```python
from core.route_registry import build_api_path, parse_api_gate, AuthPolicy

build_api_path("admin", AuthPolicy.PUBLIC, "login")
# → "/api/admin/public/login"

parse_api_gate("/api/health/session_gated")  # → "session_gated"
```

**Protocol exceptions (unchanged):** `/mcp`, `/oauth/*`, `/.well-known/*`.

Middleware behavior for `/api/{route}/{gate}/...`:

| gate | SessionGate | PublicPath |
|---|---|---|
| `public` | not gated | `auth_bypassed` |
| `session_gated` | require session (401 JSON) | — |
| `scope_required` | require session | — |
| `none` | not gated (handler self-auth) | — |

---

## Route Declaration Patterns

### 1. Structured HTTP Routes (preferred)

```python
from core.context import http_route_registry
from core.route_registry import AuthPolicy

@http_route_registry.route(
    route="plugins",
    endpoint="",  # collection root → /api/plugins/session_gated
    methods=["GET"],
    name="api_list_plugins",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.plugins",
)
async def list_plugins(request):
    return JSONResponse({"plugins": []})

@http_route_registry.route(
    route="admin",
    endpoint="login",
    methods=["POST"],
    name="admin_login",
    auth_policy=AuthPolicy.PUBLIC,
    owner="api.admin",
)
async def admin_login(request):
    ...
```

Nested endpoint after the gate:

```python
@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/tools/{tool_name}/state",
    methods=["POST"],
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.tools",
)
async def set_tool_state(request):
    ...
```

### 2. Free-form path (protocol / legacy)

Still accepted for non-API protocol routes (oauth, well-known). Prefer structured `route=` + `endpoint=` for all `/api/*` surfaces.

### 3. Imperative Registration (Plugins / WebSockets)

Structured form: `route=` + string `endpoint=` builds the path; pass the callable as `handler=`.

```python
from core.context import http_route_registry
from core.route_registry import AuthPolicy

def register_routes():
    http_route_registry.register_ws_route(
        route="terminal",
        endpoint="ws/{session_id}",
        handler=console_ws,
        name="terminal_ws",
        owner="cat_terminal_relay_plugin",
        auth_policy=AuthPolicy.NONE,
    )

    http_route_registry.register_http_route(
        route="terminal",
        endpoint="hosts",
        handler=get_terminal_hosts,
        methods=["GET"],
        name="get_terminal_hosts",
        owner="cat_terminal_relay_plugin",
        auth_policy=AuthPolicy.SESSION_GATED,
    )
```

Legacy free-form still works: `register_http_route("/api/foo/session_gated/bar", handler_fn, methods=["GET"], ...)`.
When in doubt, `build_api_path(...)` then pass as `path=`.

---

## Authorization Policies (`AuthPolicy`)

Located in `core.route_registry.AuthPolicy`:

| Policy | Description | Typical Use Cases |
|---|---|---|
| `PUBLIC` | Open to all clients without any auth checks. | Login, SignUp, portfolio public layout. |
| `SESSION_GATED` | Requires a valid admin session cookie. | Console REST under `/api/*/session_gated/*`. |
| `SCOPE_REQUIRED` | Requires specific OAuth scopes. | Scope-restricted API endpoints. |
| `NONE` | Bypasses standard session check. Handlers perform self-authentication. | WebSockets (ticket/token auth). |

---

## Hot-Reload & Mounting Lifecycle

### Unmounting Routes
```python
from core.context import http_route_registry

async def on_unload():
    http_route_registry.unmount_owner("cat_terminal_relay_plugin")
```

### Mounting Routes
```python
http_route_registry.mount_owner("cat_terminal_relay_plugin")
```

### Draining Pending
```python
http_route_registry.drain_pending()
```

---

## Verification and Testing

1. **Unit Testing**: `test/unit/test_http_route_registry.py`, `test/unit/test_api_path_builder.py`, `test/unit/test_api_path_middleware.py`, `test/unit/test_terminal_route_registration.py`.
2. **Integration Verification**: Unauthenticated `GET /api/health/session_gated` → 401 JSON; `POST /api/admin/public/login` without session → not session-gated.
3. **Frontend**: Vite/nginx proxy only `/api/`, `/oauth/`, `/mcp` (no separate `/admin/` or `/terminal/` proxy).
