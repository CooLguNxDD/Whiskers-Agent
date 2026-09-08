---
name: oauth-two-layer
description: |
  Two-layer auth architecture for the Whiskers Agent Server.
  USE FOR: OAuthService (Layer 1 inbound RS256 JWT), ExternalOAuthRelay (Layer 2 outbound PKCE per plugin),
  VaultService (pgcrypto credentials), PublicPathMiddleware, plugin manifest external_oauth block,
  PKCE flow, concurrent refresh safety, restart-safe pending auth.
  DO NOT USE FOR: deprecated Whiskers AgentOAuthProvider (see oauth-handler skill).
argument-hint: "Describe what you need: add external OAuth to a plugin, debug Layer 1 token errors, configure VaultService credentials, understand PKCE relay flow, etc."
user-invocable: true
---

# oauth-two-layer Skill

**Scope enforcement:** Layer 1 JWT scopes and `ocat_role` claims are evaluated by the unified [scope-management](../scope-management/SKILL.md) subsystem (`evaluate_access`). Role inheritance (Bug B) stamps `ocat_role` on pending auth and unions `playground_mcp_scopes(role)` into granted scopes at `_complete_authorization`.

| Layer | Direction | Class | Active when |
|-------|-----------|-------|-------------|
| **Layer 1** | Inbound (MCP clients → server) | `OAuthService` + `OAuthService_FastMCPProvider` | `OAUTH_ENABLED=true` AND `DATABASE_URL` set |
| **Layer 2** | Outbound (server → external APIs per plugin) | `ExternalOAuthRelay` + `VaultService` | `DATABASE_URL` set AND plugin has `external_oauth` in manifest |

Stdio mode: Layer 1 bypassed; uses `WHISKERS_USERNAME`/`WHISKERS_PASSWORD` directly. No `DATABASE_URL` → OAuth disabled, no legacy fallback.

---

## Layer 1 — Inbound RS256 JWT (`oauth/oauth_service.py`)

**Responsibilities:** Issue RS256 JWTs, serve JWKS at `/.well-known/jwks.json`, validate tokens (JTI revocation), implement FastMCP `OAuthProvider` adapter.

| Token type | Default TTL | Config key in `server_config.json` |
|-----------|-------------|-------------------------------------|
| Access | 15 min | `oauth.access_token_ttl_seconds` |
| Refresh | 7 days | `oauth.refresh_token_ttl_seconds` |
| Auth code | 10 min | `oauth.auth_code_ttl_seconds` |

**`token_type` claim:** `_mint_jwt()` embeds `"token_type": token_type`. `refresh_grant()` rejects non-refresh tokens:
```python
if payload.get("token_type") not in (None, "refresh"):
    raise InvalidGrantError("Token is not a refresh token")
```

**Exception hierarchy:** `InvalidClientError` · `InvalidGrantError` · `InvalidTokenError` · `ExpiredTokenError` · `RevokedTokenError`

**API Key Fallback & Scopes:**
If JWT validation fails and the token starts with `octk_`, the provider falls back to querying the active API keys via `db_layer.api_key_store.lookup_active_by_token`. If found active and unexpired, it constructs an `AccessToken` with client ID prefixed as `api-key:<key_id>` and scopes populated using `core.api_key_management.scopes.resolve_api_key_scopes(row)`.
- `NULL` / missing scopes in the database => legacy full access (returns all dynamic valid scopes floor ∪ plugin-contributed scopes via `get_valid_scopes()`).
- Empty list `[]` => deny-all access.
- Non-empty list => exactly those tokens (e.g. `terminal:use`, `plugin:job_search_plugin`, `group:job_search_plugin:apply`).

Note that API-key tokens are never cached in the provider's memory (`self._access_tokens`), ensuring that revocation takes effect instantly.


**Integration (`core/context.py`):**
```python
if OAUTH_ENABLED:
    oauth_service = OAuthService(db_url=DATABASE_URL)
    oauth_provider = OAuthService_FastMCPProvider(oauth_service)
else:
    oauth_provider = None
mcp = FastMCP("whiskers", instructions=..., auth=oauth_provider)
```

**JWKS route (`oauth/oauth_routes.py`):**
```python
@mcp.custom_route("/.well-known/jwks.json", methods=["GET"])
async def jwks_endpoint(request): ...
```

**Public-path bypass:**
```python
PUBLIC_PATH_PREFIXES = ("/.well-known/", "/oauth/plugin/")

class PublicPathMiddleware:
    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            if any(scope.get("path","").startswith(p) for p in PUBLIC_PATH_PREFIXES):
                scope["auth_bypassed"] = True
        await self._app(scope, receive, send)
```
Registered in `whiskers_mcp.py` before FastMCP app.

---

## Layer 2 — Outbound OAuth Relay (`oauth/oauth_relay.py`)

**Responsibilities:** PKCE authorize flow → persist PKCE state → callback token exchange → persist encrypted tokens → `get_token()` at runtime with auto-refresh.

**HTTP client:** Single persistent `httpx.AsyncClient` (`self._http = httpx.AsyncClient(timeout=30)`). All token-exchange calls use `self._http.post(...)` — no per-request client creation.

**Concurrent refresh safety:**
Locks are created lazily per `(plugin_id, provider)` and have a 1-hour idle TTL (`_REFRESH_LOCK_TTL = 3600`).
```python
lock_key = (plugin_id, provider)
if lock_key not in self._refresh_locks:
    self._sweep_refresh_locks()
    if len(self._refresh_locks) >= _REFRESH_LOCK_MAX:
        # Pruning safety net ...
    self._refresh_locks[lock_key] = asyncio.Lock()
self._refresh_lock_ts[lock_key] = time.monotonic()
async with self._refresh_locks[lock_key]:
    # Re-read inside lock — another waiter may have already refreshed
    return await self.get_token(plugin_id, provider, force_refresh=False)
```

Stale, unheld locks are swept synchronously during lock lazy-initialization via `_sweep_refresh_locks()`.

---

## Layer 1 Pending Auths Eviction (`oauth/oauth_service.py`)

To prevent unbounded memory growth from abandoned flows, inbound pending authorization flows carry a timestamp `self._pending_ts` and are automatically swept when a new flow is initiated using `_sweep_pending_auths()`. Entries older than `AUTH_CODE_TTL` are evicted.

**Routes (both public via PublicPathMiddleware):**
```
GET /oauth/plugin/{provider}/authorize   → build_authorize_url()
GET /oauth/plugin/{provider}/callback    → handle_callback()
```

**Plugin manifest `external_oauth` block:**
```json
{
  "name": "my_plugin",
  "external_oauth": {
    "primary": {
      "authorize_url": "${WHISKERS_CLIENT_URL}/oauth-login",
      "token_url": "${WHISKERS_API_URL}/api/v1/oauth/token",
      "client_id": "${WHISKERS_OAUTH_CLIENT_ID}",
      "scopes": ["ALL"],
      "pkce": "S256-hex"  // "S256" = RFC base64url; "S256-hex" = Whiskers Agent hex variant
    }
  },
  "required_credentials": ["WHISKERS_CLIENT_SECRET"]
}
```

**PKCE flow:**
```
GET /oauth/plugin/{provider}/authorize
  → generate code_verifier + state, compute challenge, INSERT plugin_oauth_pkce_state
  → redirect to provider

GET /oauth/plugin/{provider}/callback?code=...&state=...
  → SELECT+decrypt verifier FROM plugin_oauth_pkce_state
  → POST token_url (code, verifier, client_id, client_secret)
  → UPSERT encrypted tokens INTO plugin_oauth_tokens
```

**Using token in a tool:**
```python
from core.context import oauth_relay

async def my_tool():
    token = await oauth_relay.get_token(plugin_id="my_plugin", provider="primary")
    if token is None:
        return {"status": "auth_required",
                "authorize_url": "/oauth/plugin/primary/authorize?plugin_id=my_plugin"}
    headers = {"Authorization": f"Bearer {token}"}
    # use in safe_api_call lambda or httpx directly
```

> Tools generally use `get_registry().get_auth_headers("my_plugin")` which resolves through the relay internally.

---

## VaultService — Static Credentials (`db_layer/vault.py`)

```python
from db_layer.vault import VaultService
vault = VaultService()
await vault.set(plugin_id="my_plugin", key_name="WHISKERS_CLIENT_SECRET", value="s3cr3t")
secret = await vault.get(plugin_id="my_plugin", key_name="WHISKERS_CLIENT_SECRET")   # None if absent
secret = await vault.require(plugin_id="my_plugin", key_name="WHISKERS_CLIENT_SECRET") # raises MissingCredentialError
```

`MissingCredentialError` message includes: `whiskers vault set my_plugin WHISKERS_CLIENT_SECRET <value>`

**pgcrypto invariant:** All encrypted columns use `pgp_sym_encrypt(val, current_setting('app.master_key'))`. `MASTER_KEY` GUC injected on **every** connection via `db_layer/connection.py` `event.listen`. Never read encrypted columns as ORM attributes — always `pgp_sym_decrypt` in query.

```bash
export MASTER_KEY=$(openssl rand -base64 32)
```

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `plugin_credentials` | VaultService — static encrypted key/value per plugin |
| `plugin_oauth_pkce_state` | Temporary PKCE state (encrypted verifier, 10-min TTL) |
| `plugin_oauth_tokens` | Long-lived access/refresh tokens per plugin+provider (encrypted) |
| `plugins` | DBPluginRegistry — manifest snapshots + required_credentials |

**Credential gate at startup:** `DBPluginRegistry.validate_credentials_present(plugin_id, required_credentials)`. Missing → `PluginLoadError` → plugin skipped, others load.

---

## Testing with `test_oauth_plugin`

```bash
# 1. Set vault credential
python whiskers_mcp.py vault set test_oauth_plugin WHISKERS_CLIENT_SECRET <your_secret>
# 2. Start HTTP mode
python whiskers_mcp.py --transport http
# 3. Authorize in browser
open "http://localhost:8000/oauth/plugin/whiskers/authorize?plugin_id=test_oauth_plugin"
# 4. Call test tools via MCP
# test_layer2_token → returns token metadata
# test_layer2_call  → live API response using token
```

---

## Adding External OAuth to a New Plugin

```bash
# Step 1: Add external_oauth block to manifest.json (see block above)
# Step 2: Store secret
python whiskers_mcp.py vault set my_plugin MY_PROVIDER_CLIENT_SECRET <value>
```

```python
# Step 3: Use token in tools (plugins/my_plugin/MCPTools/my_tools.py)
from core.context import mcp, oauth_relay
import requests

@mcp.tool(description="Call my provider API")
async def call_provider(endpoint: str) -> dict:
    token = await oauth_relay.get_token(plugin_id="my_plugin", provider="my_provider")
    if token is None:
        return {"status": "auth_required",
                "message": "Visit /oauth/plugin/my_provider/authorize?plugin_id=my_plugin"}
    return safe_api_call(
        lambda: requests.get(f"https://api.provider.example.com/{endpoint}",
                             headers={"Authorization": f"Bearer {token}"}, timeout=30),
        lambda r: r.json(), context="call_provider"
    )
```

```json
// Step 4: plugin_config.json
{ "tier": "free", "plugins": ["plugins.core_mcp_plugin", "plugins.my_plugin"] }
```
No changes to `oauth_routes.py` — relay handles all plugins via `plugin_id` query param.

---

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `DATABASE_URL` | Yes (both layers) | PostgreSQL connection |
| `MASTER_KEY` | Yes (when DB set) | pgcrypto GUC — 32-byte base64 |
| `OAUTH_ENABLED` | No (auto) | Layer 1 — RS256 JWT for inbound clients |
| `MCP_SERVER_URL` | No | Builds `redirect_uri` for Layer 2 callbacks |
| `WHISKERS_CLIENT_URL` | No | Manifest `${WHISKERS_CLIENT_URL}` placeholder |
| `WHISKERS_OAUTH_CLIENT_ID` | No | Manifest `${WHISKERS_OAUTH_CLIENT_ID}` placeholder |

---

## Debugging Checklist

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `PluginLoadError: missing credential 'X'` | Vault entry missing | `vault set <plugin_id> X <value>` |
| `401` on `/oauth/plugin/` routes | `PublicPathMiddleware` not mounted | Check middleware order in `whiskers_mcp.py` |
| `pgp_sym_decrypt` error | `MASTER_KEY` mismatch | Ensure same key used to encrypt and decrypt |
| Layer 2 token `None` after callback | Callback URL mismatch | Check `MCP_SERVER_URL` matches registered OAuth app |
| `S256-hex` vs `S256` mismatch | Wrong PKCE variant | Use `"S256-hex"` for Whiskers Agent backends; `"S256"` for standard |
| `RevokedTokenError` | JTI blacklisted | Client must re-authorize |
| OAuth disabled despite `OAUTH_ENABLED=true` | `DATABASE_URL` not set | Both layers require DB |
| Refresh grant fails with `InvalidGrantError` | Missing `token_type` claim | `_mint_jwt()` must embed `"token_type": token_type` |
| `Client was not registered with scope` | Auto-registered client did not have all server valid scopes | Ensure `auto_register_client` registers/updates clients with all valid scopes. |

---

## Dropped
- Layer overview narrative paragraph (table replaced it)
- TTL config key names: kept in token table but cut prose explanation
- PublicPathMiddleware prose explanation
- HTTP client prose (kept code only)
- "pkce variant" prose section → moved to inline comment in JSON
- VaultService CLI set example (trivial command)
- test_oauth_plugin tool descriptions prose
- Step prose from "Adding External OAuth" (kept code, cut narrative)
