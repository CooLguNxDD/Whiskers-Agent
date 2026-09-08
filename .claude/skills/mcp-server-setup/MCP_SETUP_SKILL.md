---
name: mcp-server-setup
description: '**CODE PATTERN SKILL** — Whiskers Agent server bootstrap, transport modes, and module registration. USE FOR: understanding server initialization, adding new tool modules, configuring stdio vs HTTP transport, environment variable contracts. DO NOT USE FOR: OAuth specifics (use oauth-handler skill), LangGraph agent details (use langgraph-chain skill).'
---

# MCP Server Setup

## Overview

The Whiskers Agent server is built on the **FastMCP** SDK (`mcp.server.fastmcp.FastMCP`). It exposes platform tools (records, messages, schedules, etc.) over the Model Context Protocol. The server supports two transport modes: **stdio** (subprocess) for local MCP clients and **HTTP/SSE** (Uvicorn) for networked deployment.

## Architecture

```
whiskers_mcp.py          ← Entrypoint: logging, transport selection, startup
  └── MCPTools/
        __init__.py            ← Package init: imports every tool module to trigger @mcp.tool() registration
        context.py             ← Singleton FastMCP instance, config, auth helpers
        records.py             ← @mcp.tool() functions
        messages.py            ← @mcp.tool() functions
        appointments.py        ← @mcp.tool() functions
        conversations.py       ← @mcp.tool() functions
        operations.py           ← @mcp.tool() functions
        auth.py                ← (reserved) auth tool
        graph_tool.py          ← run_graph LangGraph wrapper
        oauth_provider.py      ← OAuth 2.0 provider (conditional)
        oauth_routes.py        ← /whiskers-auth/callback route (conditional)
```

## Bootstrap Sequence

1. **`whiskers_mcp.py`** calls `load_dotenv()` **before** any MCPTools import.
2. **`import MCPTools`** triggers `MCPTools/__init__.py`:
   - Imports `context.py` → reads env vars, creates the `mcp` FastMCP singleton.
   - Imports each tool module (records, messages, etc.) → `@mcp.tool()` decorators register tools on the singleton.
   - If `OAUTH_ENABLED`, imports `oauth_routes.py` to register the callback route.
3. **`whiskers_mcp.py`** reads transport mode from CLI args and starts the server.

### Key Rule: Module-Level Registration

All tools are registered at **import time** via `@mcp.tool()` decorators on module-level functions. There is no explicit registration step — importing the module is the registration.

```python
# MCPTools/__init__.py
from . import records        # registers create_record, get_record, etc.
from . import messages       # registers send_message, get_messages, etc.
from . import graph_tool     # registers run_graph

if OAUTH_ENABLED:
    from . import oauth_routes  # registers /whiskers-auth/callback
```

## Adding a New Tool Module

1. Create `MCPTools/new_module.py`.
2. Import the shared `mcp` instance from context:
   ```python
   from .context import mcp, API_URL, PROJECT_ID, WORKSPACE_ID, _auth_headers
   from .api_utils import safe_api_call
   ```
3. Define functions with `@mcp.tool()`:
   ```python
   @mcp.tool()
   def my_new_tool(param: str) -> dict[str, Any]:
       """Tool docstring — becomes the tool description for MCP clients."""
       ...
   ```
4. Add the import to `MCPTools/__init__.py`:
   ```python
   from . import new_module  # noqa: F401
   ```
5. If the tool should be available in the LangGraph agent, also import and register it in `graph_tool.py` → `_get_graph()`.
6. Add a TOOL_SCHEMAS entry in `MCPTools/langgraph_flows/tool_schemas.py`.

## Transport Modes

### stdio (default)
```bash
python whiskers_mcp.py
# or explicitly:
python whiskers_mcp.py --transport stdio
```
Used when the MCP client spawns the server as a subprocess (e.g., Claude Desktop, VS Code).

### HTTP/SSE
```bash
python whiskers_mcp.py --transport http --host 0.0.0.0 --port 8000
```
Runs Uvicorn with the FastMCP ASGI app. The MCP endpoint is mounted at `/mcp`. When OAuth is enabled, the server also serves `/authorize`, `/token`, `/register`, and `/whiskers-auth/callback`.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `WHISKERS_API_URL` | Yes | Whiskers Agent backend base URL |
| `WHISKERS_PROJECT_ID` | Yes | Default project ID |
| `WHISKERS_WORKSPACE_ID` | Yes | Default workspace ID |
| `WHISKERS_USERNAME` | No* | Backend login username |
| `WHISKERS_PASSWORD` | No* | Backend login password |
| `OAUTH_ENABLED` | No | `true`/`false` override; auto-detected if absent |
| `MCP_SERVER_URL` | No | Public URL of this server (default `http://localhost:8000`) |
| `WHISKERS_CLIENT_URL` | No | Whiskers Agent Angular client URL for OAuth login page |
| `WHISKERS_OAUTH_CLIENT_ID` | No | Backend OAuth client ID |
| `OAUTH_REDIRECT_URI` | No | Override callback URI (default `{MCP_SERVER_URL}/oauth/plugin/whiskers/callback`) |
| `LLM_PROVIDER` | No | `openai` (default), `anthropic`, or `gemini` |
| `LLM_MODEL` | No | Override model name |
| `OPENAI_API_KEY` | No | Required if LLM_PROVIDER=openai |
| `ANTHROPIC_API_KEY` | No | Required if LLM_PROVIDER=anthropic |
| `GOOGLE_API_KEY` | No | Required if LLM_PROVIDER=gemini |

*If `WHISKERS_USERNAME`/`WHISKERS_PASSWORD` are absent, OAuth is auto-enabled.

## Transport Security

The server configures `TransportSecuritySettings` with DNS rebinding protection. Allowed hosts are built dynamically from `MCP_SERVER_URL` so reverse-proxy deployments work:

```python
_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "<MCP_SERVER_URL host>"],
)
```

## FastMCP Singleton Pattern

A single `FastMCP` instance is created in `context.py` and shared across all modules. This instance is the central registry for tools, routes, and auth settings.

```python
# context.py — one of two branches depending on OAUTH_ENABLED
mcp = FastMCP(
    "whiskers",
    instructions="Whiskers Agent platform platform – ...",
    mount_path="/mcp",
    # auth_server_provider=oauth_provider,  ← only when OAuth is enabled
    # auth=AuthSettings(...),               ← only when OAuth is enabled
    transport_security=_transport_security,
)
```

Every tool module does `from .context import mcp` and uses `@mcp.tool()` to register.


---

## Host bootstrap

Cold machines: `./install.sh --yes` (POSIX) or `.\install.ps1 -Yes` (Windows). uv creates `.venv` with CPython 3.11 and installs `requirements.txt`, then execs `python terminal/script/setup.py all`. `--no-uv` falls back to `python3 -m venv` + pip. Do not `cp .env_sample .env` — `MASTER_KEY=your-key` will encrypt the keypair under the placeholder. Resume state: `.whiskers/setup-state.json` (gitignored). `--from STEP` / `--force`.

## Setup TUI

`python -m terminal` — rich interactive hub. Shared helpers live in `terminal/tui/ui.py` (`menu`, `status_header`, `breadcrumb`, `run_with_progress`, `glyph` ASCII fallback, `confirm_discard`). First-run wizard when `.env` or `config/plugin_config.json` is missing. Guided setup shows resume `[done]/[failed]/[pending]`. Live Settings uses one `asyncio.run` around the submenu. Unknown `WHISKERS_TUI_THEME` warns and falls back to mocha. Main loop still uses `Prompt.ask` / `Confirm.ask` so `test_setup_tui.py` mocks keep working.

## Persistence, Restarts & API Key Safety

- `api_keys` + `auth_keypairs` live in the postgres named volume (`postgres_data` in compose).
- **Never** `docker compose down -v` (or `docker volume rm ...`): this destroys the volume and all API keys + encrypted keypairs. Restart only with `docker restart whiskers-mcp-server`.
- Keep the checkout directory name and compose project (`-p`) stable — volume names are scoped to project.
- `MASTER_KEY` (in .env / compose) must be byte-for-byte identical forever; it is used to pgcrypto-encrypt private keys and API key values. A boot guard aborts startup with a critical log on decrypt failure (see `phase_keypair_guard`).
- `samesite="lax"` is used for session cookies (was strict) to survive top-level navigation from tunnel hosts.
- The placeholder for API keys in the login UI is `octk_...` (not `whiskers_`).
- API keys support scopes (stored in a nullable `scopes` JSONB column). Scopes can be `plugin:<plugin_id>` (coarse plugin control), `group:<plugin_id>:<tag>` (fine-grained tag/group control), or OAuth scopes (`terminal:use`, `terminal:host`, `admin`, etc.).
- NULL `scopes` grants legacy full access (all valid OAuth scopes); `[]` denies all access; a non-empty list grants exactly those scopes.
- Enforcement occurs at `core_graph/node/permission_gate.py` (before step builder execution) and in connection handlers (terminal handshake auth, sandbox tools, and terminal tools).
- `scripts/reseed.py` (data-only) leaves api_keys/auth_keypairs intact on purpose; only --full or volume loss removes them.
- Revoke on an admin API key now performs rotation: `POST /api/auth/api-keys/{key_id}/revoke` calls `rotate_api_key` (in `db_layer/api_key_store.py`) which does soft-revoke then `create_api_key` (same name) and returns the new `{key_id, token, ...}`. The admin page (`routes/api-keys.tsx`) shows the new secret in the existing one-time copy panel. Old token stops working immediately. See also `api/api_key_routes.py` and updated tests.
- See CLAUDE.md "Persistence & API Key / Session Safety" and the guard implemented in core/bootstrap.py.
