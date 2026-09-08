# Whiskers Agent — Two-Layer Auth Refactor (PG-Native)
## Setup Guide

**Last Updated:** May 5, 2026

---

## Overview

This guide walks through the complete setup of the two-layer OAuth architecture for the Whiskers Agent server:

| Layer | Direction | What it does |
|-------|-----------|--------------|
| **Layer 1** | Inbound — MCP clients → server | RS256 JWT OAuth 2.1 issued to MCP clients (Claude Desktop, agents) |
| **Layer 2** | Outbound — server → external APIs | Per-plugin PKCE relay storing tokens encrypted in PostgreSQL (pgcrypto) |

Both layers require **PostgreSQL with pgcrypto** and a **MASTER_KEY** environment variable. Layer 1 additionally requires `OAUTH_ENABLED=true`.

---

## Prerequisites

### 1. PostgreSQL 14+ with pgcrypto

pgcrypto must be enabled in the target database:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;   -- for embedding support
```

Verify:
```sql
SELECT extname FROM pg_extension WHERE extname IN ('pgcrypto', 'vector');
```

Both extensions must appear in the output before proceeding.

### 2. Python Environment

```bash
pip install -r requirements.txt
```

### 3. Environment File

Copy the example and fill in required values:

```bash
cp .env.example .env
```

Minimum required variables for two-layer auth:

```dotenv
# Database (required for both layers)
DATABASE_URL=postgresql://mcp:mcp@localhost:5432/mcp

# pgcrypto encryption key (required when DATABASE_URL is set)
MASTER_KEY=<generate below>

# external API backend (required for tool API calls)
PLUGIN_API_URL=https://your-api-instance.example.com
DEFAULT_PROJECT_ID=1
DEFAULT_SCOPE_ID=1

# Layer 2 — plugin OAuth provider (required for external_oauth plugins)
PLUGIN_CLIENT_URL=https://your-client.example.com
PLUGIN_OAUTH_CLIENT_ID=your-oauth-client-id
MCP_SERVER_URL=http://localhost:8000   # must match registered redirect_uri
```

---

## Step 1 — Generate the MASTER_KEY

The `MASTER_KEY` is the symmetric encryption key used by pgcrypto for all encrypted columns (`plugin_credentials`, `plugin_oauth_tokens`, `plugin_oauth_pkce_state`).

**Generate once and store permanently. Losing this key means losing all encrypted data.**

```bash
# Linux / macOS / WSL
openssl rand -base64 32

# PowerShell
[Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
```

Add to `.env`:

```dotenv
MASTER_KEY=<output from above>
```

> **Critical:** The same `MASTER_KEY` must be used for every server instance that connects to the same database. Rotating this key requires re-encrypting all vault entries.

---

## Step 2 — Run Database Migrations

Alembic manages the schema. Migrations create the OAuth tables, vault tables, embedding tables, and bearer token tables.

```bash
# Linux / macOS
./scripts/migrate.sh upgrade head

# Windows
scripts\migrate.bat upgrade head
```

Expected output includes:
```
INFO  [alembic.runtime.migration] Running upgrade ... -> <rev>, create oauth tables
INFO  [alembic.runtime.migration] Running upgrade ... -> <rev>, add plugin credentials and oauth relay tables
INFO  [alembic.runtime.migration] Running upgrade ... -> <rev>, add embeddings and cache tables
```

Verify the schema:
```bash
./scripts/migrate.sh current      # shows current revision
./scripts/migrate.sh history      # shows full migration chain
```

---

## Step 3 — Configure Layer 1 (Inbound RS256 JWT)

Layer 1 secures inbound connections from MCP clients (Claude Desktop, LangGraph agents, etc.) using RS256 JWTs.

### 3a. Enable OAuth

```dotenv
OAUTH_ENABLED=true
```

When `OAUTH_ENABLED=true` AND `DATABASE_URL` is set, the server mounts `OAuthService_FastMCPProvider` as the FastMCP auth provider. In stdio mode, Layer 1 is **always bypassed** regardless of this flag.

### 3b. RS256 Key Pair Generation

The server auto-generates an RS256 key pair on first startup and persists it to the `auth_keypairs` table (encrypted at rest with pgcrypto). No manual key generation is required.

If you need to rotate keys:
```bash
python whiskers_mcp.py rotate-keys
```

### 3c. Verify JWKS Endpoint

Start the server in HTTP mode and verify the JWKS endpoint is reachable without auth:

```bash
python whiskers_mcp.py --transport http --port 8000

# In another terminal:
curl http://localhost:8000/.well-known/jwks.json
```

Expected response:
```json
{
  "keys": [
    {
      "kty": "RSA",
      "use": "sig",
      "kid": "...",
      "alg": "RS256",
      "n": "...",
      "e": "AQAB"
    }
  ]
}
```

### 3d. Token Lifetimes (defaults)

| Token type | Lifetime |
|------------|----------|
| Access token | 15 minutes |
| Refresh token | 7 days |
| Auth code | 10 minutes |

These are configured in `oauth/oauth_service.py` constants.

---

## Step 4 — Configure Layer 2 (Outbound OAuth Relay)

Layer 2 handles plugin-specific outbound OAuth flows — the server acts as an OAuth relay, storing tokens for each plugin+provider pair encrypted in the database.

### 4a. Register Your OAuth Application (Whiskers Agent Backend)

In the external service admin panel, create an OAuth application with:

- **Redirect URI**: `{MCP_SERVER_URL}/oauth/plugin/{plugin_id}/callback`
  - Example: `http://localhost:8000/oauth/plugin/{plugin_id}/callback`
- **PKCE**: enabled (S256 variant, hex-encoded for Whiskers Agent)
- **Scopes**: `ALL` (or your required subset)

Copy the **Client ID** and **Client Secret**.

### 4b. Set Environment Variables

```dotenv
PLUGIN_OAUTH_CLIENT_ID=<client_id_from_step_4a>
PLUGIN_CLIENT_URL=https://your-frontend.example.com
MCP_SERVER_URL=http://localhost:8000   # or your production URL
```

### 4c. Store Client Secret in the Vault

The client secret is stored encrypted in the database, not in `.env`:

```bash
# For core_mcp_plugin (if it uses external_oauth)
python whiskers_mcp.py vault set core_mcp_plugin PLUGIN_CLIENT_SECRET <your_client_secret>

# For test_oauth_plugin (smoke test)
python whiskers_mcp.py vault set test_oauth_plugin PLUGIN_CLIENT_SECRET <your_client_secret>
```

Verify the credential was stored:
```bash
python whiskers_mcp.py vault get test_oauth_plugin PLUGIN_CLIENT_SECRET
# Should print: PLUGIN_CLIENT_SECRET = [SET]
```

### 4d. Plugin Manifest — `external_oauth` Block

Each plugin that needs Layer 2 tokens declares its provider in `manifest.json`:

```json
{
  "name": "my_plugin",
  "version": "1.0.0",
  "tier": "free",
  "external_oauth": {
    "external_api": {
      "authorize_url": "${PLUGIN_CLIENT_URL}/oauth-login",
      "token_url": "${PLUGIN_API_URL}/api/v1/oauth/token",
      "client_id": "${PLUGIN_OAUTH_CLIENT_ID}",
      "scopes": ["ALL"],
      "pkce": "S256-hex"
    }
  },
  "required_credentials": ["PLUGIN_CLIENT_SECRET"]
}
```

**`pkce` values:**
- `"S256-hex"` — provider-specific variant (hex-encoded code challenge). Use for all external API backends.
- `"S256"` — RFC 7636 standard (base64url-encoded). Use for third-party providers (GitHub, Google, etc.).

`${VAR}` placeholders are resolved from environment variables at plugin load time.

---

## Step 5 — Configure `plugin_config.json`

Ensure your plugins are listed:

```json
{
  "tier": "free",
  "plugins": [
    "plugins.core_mcp_plugin",
    "plugins.report_plugin",
    "plugins.test_oauth_plugin"
  ]
}
```

Active plugins are validated at startup. Plugins with missing vault credentials are **skipped with a warning** — other plugins still load.

---

## Step 6 — Docker Compose (Full Stack)

For local development with PostgreSQL + pgAdmin:

```bash
# Build and start all services
docker compose up -d

# Check logs
docker compose logs -f whiskers-mcp-server
```

The `docker-compose.yml` starts:
- **PostgreSQL 16** with pgvector on `:5432`
- **Whiskers Agent server** on `:10000`
- **pgAdmin** on `:5050` (admin@whiskers.local / admin)

### Docker `.env` file

Create `.env.docker` (gitignored):

```dotenv
DATABASE_URL=postgresql://mcp:mcp@db:5432/mcp
MASTER_KEY=<same key as generated in Step 1>
OAUTH_ENABLED=true
MCP_SERVER_URL=http://localhost:10000
PLUGIN_API_URL=https://your-api-instance.example.com
DEFAULT_PROJECT_ID=1
DEFAULT_SCOPE_ID=1
PLUGIN_CLIENT_URL=https://your-client.example.com
PLUGIN_OAUTH_CLIENT_ID=your-client-id
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

After first start, run migrations inside the container:

```bash
docker compose exec whiskers-mcp-server ./scripts/migrate.sh upgrade head

# Set vault credentials inside the container
docker compose exec whiskers-mcp-server python whiskers_mcp.py vault set test_oauth_plugin PLUGIN_CLIENT_SECRET <secret>
```

---

## Step 7 — End-to-End Smoke Test

### 7a. Verify Layer 1

```bash
# Start server
python whiskers_mcp.py --transport http --port 8000

# JWKS reachable
curl http://localhost:8000/.well-known/jwks.json

# Authorize endpoint exists
curl -I http://localhost:8000/oauth/authorize
# Expected: 400 Bad Request (missing params) — not 401 or 404
```

### 7b. Verify Layer 2 — Token Flow

Using `test_oauth_plugin`:

```bash
# 1. Open the authorize URL in a browser
open "http://localhost:8000/oauth/plugin/{plugin_id}/authorize?plugin_id=test_oauth_plugin"

# 2. Complete the external OAuth login flow
#    Browser redirects to /oauth/plugin/{plugin_id}/callback automatically
#    Server stores encrypted token in plugin_oauth_tokens

# 3. Call test_layer2_token via MCP to verify token was stored
# 4. Call test_layer2_call to verify the token works against the live API
```

### 7c. Verify via Database

```sql
-- Check plugin credentials exist
SELECT plugin_id, key_name, created_at FROM plugin_credentials;

-- Check token was stored after OAuth callback
SELECT plugin_id, provider, updated_at FROM plugin_oauth_tokens;

-- Verify pgcrypto is working (should return the actual secret, not ciphertext)
SELECT pgp_sym_decrypt(
  secret_value::bytea,
  current_setting('app.master_key')
) FROM plugin_credentials WHERE plugin_id = 'test_oauth_plugin' LIMIT 1;
```

---

## Troubleshooting

### MASTER_KEY Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `pgp_sym_decrypt: no decryption key available` | `app.master_key` GUC not set | Verify `MASTER_KEY` env var is set; check `db_layer/connection.py` event listener |
| Decrypted value is garbage | Wrong `MASTER_KEY` | The key used to decrypt must match the key used to encrypt — both must be the same |
| `ERROR: function pgp_sym_encrypt does not exist` | pgcrypto not installed | `CREATE EXTENSION pgcrypto;` in the database |

### Layer 1 Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `/.well-known/jwks.json` returns `401` | `PublicPathMiddleware` not mounted | Check middleware registration order in `whiskers_mcp.py` |
| `RevokedTokenError` | JTI blacklisted | Client must re-authorize; check `oauth_access_tokens` table for revocation |
| Layer 1 active in stdio mode | Not expected behavior | Layer 1 is always bypassed in stdio; check `OAUTH_ENABLED` and transport mode |

### Layer 2 Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `PluginLoadError: missing credential 'PLUGIN_CLIENT_SECRET'` | Vault entry absent | `vault set <plugin_id> PLUGIN_CLIENT_SECRET <value>` |
| Callback URL mismatch | `MCP_SERVER_URL` wrong | Set `MCP_SERVER_URL` to match the redirect URI registered in the OAuth app |
| `S256` challenge rejected by Whiskers Agent | Wrong PKCE variant | Use `"pkce": "S256-hex"` in the manifest for external API backends |
| `get_token()` returns `None` after callback | State mismatch or expired PKCE state | PKCE state TTL is 10 minutes — retry the authorize flow |
| Plugin skipped at startup | Missing credential | Check startup logs for `PluginLoadError` with `vault set` instructions |

---

## Architecture Reference

```
MCP Client (Claude Desktop / agent)
    │
    │  Bearer Token (RS256 JWT — Layer 1)
    ▼
┌─────────────────────────────────────────┐
│  Whiskers Agent Server (HTTP mode)         │
│                                         │
│  PublicPathMiddleware                   │
│  ├── /.well-known/*  ─────── no auth   │
│  └── /oauth/plugin/* ─────── no auth   │
│                                         │
│  OAuthService_FastMCPProvider (L1)      │
│  ├── POST /oauth/authorize              │
│  ├── POST /oauth/token                  │
│  └── GET  /.well-known/jwks.json        │
│                                         │
│  ExternalOAuthRelay (L2)                │
│  ├── GET /oauth/plugin/{p}/authorize    │
│  └── GET /oauth/plugin/{p}/callback     │
│                                         │
│  MCP Tools                              │
│  └── oauth_relay.get_token() ──────────┼──► plugin_oauth_tokens (PostgreSQL)
│                                         │
└─────────────────────────────────────────┘
    │
    │  Bearer Token (plugin OAuth token — Layer 2)
    ▼
External API (external API backend, etc.)
```

---

## Related Files

| File | Purpose |
|------|---------|
| `oauth/oauth_service.py` | Layer 1 — `OAuthService` + `OAuthService_FastMCPProvider` |
| `oauth/oauth_relay.py` | Layer 2 — `ExternalOAuthRelay` PKCE flow |
| `oauth/oauth_routes.py` | JWKS + plugin authorize/callback routes + `PublicPathMiddleware` |
| `db_layer/vault.py` | `VaultService` — pgcrypto-backed static credential store |
| `db_layer/models.py` | ORM models: `PluginCredential`, `PluginOAuthToken`, `PluginOAuthPKCEState`, `AuthKeypair` |
| `db_layer/connection.py` | MASTER_KEY GUC injection on every DBAPI connection |
| `plugin_loader/plugin_loader.py` | `validate_credentials_present()` — credential gate at startup |
| `core/context.py` | `oauth_provider` + `oauth_relay` singletons |
| `whiskers_mcp.py` | Middleware registration order, vault CLI commands |
| `plugins/test_oauth_plugin/` | Smoke-test plugin for Layer 2 relay |

---

## Quick Reference — Common Commands

```bash
# Generate MASTER_KEY
openssl rand -base64 32

# Run migrations
./scripts/migrate.sh upgrade head          # Linux/macOS
scripts\migrate.bat upgrade head           # Windows

# Vault operations
python whiskers_mcp.py vault set <plugin_id> <key> <value>
python whiskers_mcp.py vault get <plugin_id> <key>
python whiskers_mcp.py vault list <plugin_id>
python whiskers_mcp.py vault delete <plugin_id> <key>

# Start server
python whiskers_mcp.py --transport http --port 8000

# Docker
docker compose up -d
docker compose exec whiskers-mcp-server ./scripts/migrate.sh upgrade head
docker compose exec whiskers-mcp-server python whiskers_mcp.py vault set test_oauth_plugin PLUGIN_CLIENT_SECRET <secret>

# Reset database (keeps schema)
./scripts/reseed.sh

# Full schema reset
./scripts/reseed.sh --full
```
