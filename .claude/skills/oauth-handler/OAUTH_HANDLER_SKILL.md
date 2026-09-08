---
name: oauth-handler
description: '**CODE PATTERN SKILL** — plugin OAuth 2.0 + PKCE delegated authentication flow. USE FOR: understanding the OAuth authorization dance, callback handling, token exchange, client registration persistence, adding new OAuth scopes, debugging auth failures. DO NOT USE FOR: env-var credential login (see context.py _get_token), general MCP server setup (use mcp-server-setup skill).'
---

# OAuth Handler

## Overview

The Whiskers Agent server implements **OAuth 2.0 Authorization Code + PKCE** by delegating to the external API backend. The MCP server acts as both an OAuth Authorization Server (for MCP clients) and an OAuth Client (toward the external API backend). This dual role is the key architectural insight.

## Files

| File | Role |
|---|---|
| `MCPTools/context.py` | OAuth auto-detection, provider instantiation, `mcp` wiring |
| `MCPTools/oauth_provider.py` | `LegacyOAuthProvider` — full OAuth AS implementation |
| `MCPTools/oauth_routes.py` | Starlette callback route `/oauth/callback` |

## OAuth Flow Diagram

```
MCP Client          MCP Server                Whiskers Agent Client (Angular)       Whiskers Agent Backend
    │                    │                            │                          │
    │─── /authorize ────►│                            │                          │
    │                    │── redirect ───────────────►│                          │
    │                    │   /oauth-login?...         │                          │
    │                    │                            │── POST /login/oauth ───►│
    │                    │                            │◄── {redirect: callback} ─│
    │                    │◄── /oauth/callback ──│                          │
    │                    │    ?code=BACKEND&state=X   │                          │
    │◄── redirect ──────│                            │                          │
    │    ?code=MCP&state │                            │                          │
    │                    │                            │                          │
    │─── POST /token ──►│                            │                          │
    │    code=MCP        │── POST /oauth/token ─────────────────────────────────►│
    │                    │   code=BACKEND+verifier    │                          │
    │                    │◄── {access_token} ───────────────────────────────────│
    │◄── {access_token} ─│                            │                          │
```

## Step-by-Step

### 1. Auto-Detection (context.py)

OAuth is enabled when credentials are absent or explicitly forced:

```python
_oauth_env = os.environ.get("OAUTH_ENABLED", "").lower()
if _oauth_env == "true":
    OAUTH_ENABLED = True
elif _oauth_env == "false":
    OAUTH_ENABLED = False
else:
    OAUTH_ENABLED = not (USERNAME and PASSWORD)
```

When enabled, `context.py` instantiates `LegacyOAuthProvider` and passes it to FastMCP:

```python
mcp = FastMCP(
    "plugin",
    auth_server_provider=oauth_provider,
    auth=AuthSettings(
        issuer_url=MCP_SERVER_URL,
        resource_server_url=f"{MCP_SERVER_URL}/mcp",
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["plugin"],
            default_scopes=["plugin"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    ),
    ...
)
```

### 2. Client Registration

MCP clients register via the standard `/register` endpoint. The provider persists clients to `.mcp_clients.json`:

```python
async def register_client(self, client_info):
    self._clients[client_info.client_id] = client_info
    self._save_clients()  # writes to .mcp_clients.json
```

### 3. Authorization (`/authorize`)

When an MCP client hits `/authorize`:

1. Sweeps expired entries older than 10 minutes from `_pending_auths` and `_pending_pkce`.
2. Provider generates a random `auth_state` and stores the pending request.
3. Provider generates a **PKCE pair** (`code_verifier` + `code_challenge`) for the backend leg.
4. Redirects to `{PLUGIN_CLIENT_URL}/oauth-login?response_type=code&client_id=...&code_challenge=...&state=auth_state`.

```python
async def authorize(self, client, params) -> str:
    self._sweep_pending()
    auth_state = secrets.token_urlsafe(32)
    self._pending_auths[auth_state] = { ... }
    code_verifier, code_challenge = _generate_pkce()
    self._pending_pkce[auth_state] = {"code_verifier": ..., "code_challenge": ...}
    return f"{self._client_url}/oauth-login?{urlencode(oauth_params)}"
```

### 4. Callback (`/oauth/callback`)

After the user logs in on the external OAuth client, the backend issues a redirect with `code=BACKEND_CODE&state=auth_state`. The callback route in `oauth_routes.py` receives it:

```python
@mcp.custom_route("/oauth/callback", methods=["GET"])
async def plugin_oauth_callback(request: Request) -> Response:
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    redirect_url, err = oauth_provider.handle_backend_callback(state, code)
    return RedirectResponse(url=redirect_url, status_code=303)
```

`handle_backend_callback` creates an **MCP auth code** mapped to the backend code + PKCE verifier, then redirects the MCP client.

### 5. Token Exchange (`/token`)

The MCP client sends its MCP auth code to `/token`. The provider:

1. Looks up the mapped backend code + code_verifier.
2. Forwards to `POST {API_URL}/api/v1/oauth/token` with `grant_type=authorization_code`, `code=BACKEND_CODE`, `code_verifier=...`.
3. Wraps the backend's `access_token` into an MCP `OAuthToken` response.

```python
async def exchange_authorization_code(self, client, authorization_code) -> OAuthToken:
    backend_info = self._code_to_backend.pop(code, None)
    resp = requests.post(f"{self._api_url}/api/v1/oauth/token", json={
        "grant_type": "authorization_code",
        "code": backend_info["backend_code"],
        "redirect_uri": backend_info["backend_redirect_uri"],
        "code_verifier": backend_info["code_verifier"],
    })
    return OAuthToken(access_token=..., token_type="Bearer", expires_in=...)
```

### 6. Token Usage in Tools

When OAuth is enabled, `_get_token()` in `context.py` reads the access token from the MCP auth middleware context:

```python
from mcp.server.auth.middleware.auth_context import get_access_token
access = get_access_token()
return access.token, True  # is_oauth=True
```

The `_auth_headers()` helper sets `Authorization: Bearer <token>` for OAuth tokens vs. a legacy `authentication` header for env-var tokens.

## PKCE Implementation Detail

The Some backends use `hex(sha256(verifier))` instead of standard base64url:

```python
def _generate_pkce() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)[:96]
    code_challenge = hashlib.sha256(code_verifier.encode("ascii")).hexdigest()
    return code_verifier, code_challenge
```

## In-Memory Stores

| Store | Keyed By | Contents |
|---|---|---|
| `_clients` | client_id | Registered OAuth clients (persisted to `.mcp_clients.json`) |
| `_pending_auths` | auth_state | Pending MCP authorize requests (expires after 10m TTL) |
| `_pending_pkce` | auth_state | PKCE pairs for pending logins (expires after 10m TTL) |
| `_auth_codes` | mcp_code | Issued MCP authorization codes |
| `_code_to_backend` | mcp_code | Backend code + verifier mapping |
| `_access_tokens` | token string | Issued access tokens (with expiry) |

## Common Debugging Points

- **"Invalid or expired authorization state"** → `auth_state` not found in `_pending_auths`. Check timing or duplicate callback.
- **Backend token exchange 400** → PKCE mismatch. Verify `code_verifier` matches what was sent as `code_challenge`.
- **OAuth auto-enabled unexpectedly** → `PLUGIN_API_USERNAME`/`PLUGIN_API_PASSWORD` are empty in `.env`.
- **Refresh tokens** → Not supported. Clients must re-authorize when the token expires.
