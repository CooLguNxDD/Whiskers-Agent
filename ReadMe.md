# Whiskers Agent MCP Server

Plugin-driven [Model Context Protocol](https://modelcontextprotocol.io/) platform. FastMCP over `stdio` and HTTP, LangGraph/GOAP orchestration, two-layer OAuth, an encrypted credential vault, hot-swappable plugins, and a React admin console. The core server is domain-agnostic; domain integrations live in `plugins/`.

- **Setup**: [SetupGuide.md](./SetupGuide.md) · [CONTRIBUTING.md](./CONTRIBUTING.md)
- **Contributor index**: [CLAUDE.md](./CLAUDE.md) (architecture, guardrails, file map, skills)
- **Knowledge Graph**: [graphify-out/](./graphify-out/) (local audit report & interactive visualization; gitignored)

## Table of Contents
- [Overview](#overview)
- [Core Features](#core-features)
- [Plugins](#plugins)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Plugin Credential Vault](#plugin-credential-vault)
- [GoapAgent (graph MCP + headless CLI)](#goapagent-graph-mcp--headless-cli)
- [Running the Server](#running-the-server)
- [Docker Deployment](#docker-deployment)
- [Project Structure](#project-structure)
- [Development Utilities](#development-utilities)
- [Tech Stack](#tech-stack)

## Overview

MCP clients (VS Code, Claude Desktop, custom agent runners) connect to Whiskers Agent to discover catalog operations, run multi-step workflows, and manage the server through the Cat Admin console.

Canonical process entry is `whiskers_agent_mcp.py` (FastMCP startup, middleware, routes, tools), with `whiskers_mcp.py` serving as a backward-compatibility shim. `agent.py` is the interactive / headless CLI. Boot is phased in `core/bootstrap/` (keypair guard, plugin discovery, registry reconcile, scope health).

Live operations resolve by `(plugin_id, operation_id)` in `OperationCatalog`. Gateway mode hides plugin tools and exposes `run_graph`, `discover_tools`, `authenticate`, and `complete_authentication`.

## Core Features

### Orchestration
- `run_graph` — server-owned LangGraph loop (`core_graph/runtime/mode_router.py` → `oneshot_cli` | `root`)
- Specialist **FlowSpec** pipelines (`core_graph/subgraphs/specialist/`) — plugins declare `flow_specs/*.json`; GOAP can dispatch a flow as `specialist/<flow_id>`
- Spec-driven **model roles** (`core_graph/model_roles/`) — per-node ladders over the LLM pool
- `discover_tools` — live catalog preview for gateway mode
- **GoapAgent** — the same graph nodes as MCP tools, plus optional headless CLI spawn (`claude`)

### Platform
- Hot-swap plugin loader (`core/plugin_loader/`) with toposort, scopes, skills, and event-bus lifecycle
- Two-layer auth: Layer 1 inbound RS256 JWT (`oauth/`), Layer 2 per-plugin PKCE relay; API keys and plugin secrets in a pgcrypto vault
- Unified scope grammar (`core/scope_management/`) — `core:…`, `plugin:…` / `group:…` / `op:…`, plugin gates
- HTTP/WS route registry, catalog REST (`/api/catalog/session_gated`, OpenAPI export, execute), analytics, playground
- Artifact store (MinIO + tenant `short_id` links) and hybrid search (`pgvector` + FTS)
- CLI-as-LLM provider: `LLM_PROVIDER=claude-cli`
- VS Code extension (`whiskers-vscode/`) for gated PTY / terminal relay

## Plugins

Shipped under `plugins/`. Each plugin owns its models, migrations, MCP tools, and tests — never core `db_layer/` or top-level `test/`. Plugins do not import siblings; they dispatch through the catalog.

| Plugin | Role |
|---|---|
| `portfolio_plugin` | Discovery → compose → layout jury → bake. Job-tailored layouts, fish-tank GenUI, visitor ask FlowSpecs |
| `job_search_plugin` | Posting ingest, offer fit, resume/cover tailoring, Career-Ops apply FlowSpec (`career_ops_apply_v1`) |
| `search_plugin` | Web search (Tavily, then Brave) plus indexed document search |
| `memory_plugin` | MCP façade over tenant-scoped memory namespaces |
| `jules_plugin` | Jules review-fleet / cloud-agent tools |
| `cat_terminal_relay_plugin` | Sandboxed command execution, host PTY, step-up auth |
| `world_semantic_plugin` | Semantic world / hex-grid encoding and tools |

Claude Code marketplace plugins live in `claude-plugins/` (`portfolio-gen`, `world-context`) under the `whiskers-oct` marketplace catalogue (`.claude-plugin/marketplace.json`).

## Prerequisites
- Python 3.11+
- PostgreSQL (embeddings, vault, LangGraph checkpointer)
- Optional: LLM API key (OpenAI, Anthropic, Gemini / Vertex) for `run_graph` / GoapAgent node LLMs
- Optional: Claude Code (`claude` on PATH) + `CLAUDE_CODE_OAUTH_TOKEN` for GoapAgent Mode B / `LLM_PROVIDER=claude-cli` (installed in Docker by default)
- Optional: plugin credentials (Tavily, Jules, …) via the vault or `.env`

## Installation

Docker daemon must be running. Do **not** copy `.env_sample` to `.env` by hand —
the sample ships `MASTER_KEY=your-key`, which the server will accept and then
encrypt the keypair under that placeholder. `setup.py env` (and the installer)
replace it with a real key.

**One command (recommended):**

```bash
# Linux / macOS — uv venv + pip + setup.py all
./install.sh --yes
# optional: ./install.sh --yes --provider openai
# Windows PowerShell
.\install.ps1 -Yes
```

`make install` / `make install-dev` wrap the same path. Re-running is safe
(resume skips completed steps; pass `--force` to redo). `--no-uv` uses
`python3 -m venv` + pip; `--skip-deps` jumps straight to `setup.py`.

**Manual (if you already have a 3.11 venv):**

```bash
python terminal/script/setup.py env     # writes .env with a real MASTER_KEY
python terminal/script/setup.py all -y  # or omit -y to finish in the TUI
```

## Configuration

### Server
```env
DATABASE_URL=postgresql://mcp:mcp@localhost:5432/mcp
MASTER_KEY=<32-byte-base64>
MCP_SERVER_URL=http://localhost:10000
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key
```

### OAuth
```env
WHISKERS_OAUTH_ENABLED=true
MCP_SERVER_URL=http://localhost:10000
WHISKERS_CLIENT_URL=https://your-client.example
WHISKERS_OAUTH_CLIENT_ID=your_client_id
```

### LangGraph / LLM Configuration
Required for `run_graph`, GoapAgent node LLMs, and the interactive CLI agent:
```env
LLM_PROVIDER=openai # openai | anthropic | gemini | gemini-vertex | claude-cli
OPENAI_API_KEY=your_key
# For gemini-vertex:
# GOOGLE_VERTEX_API_KEY=your_vertex_api_key_here
# Optional:
# LLM_MODEL=gpt-4o

# Headless Claude Code inside Docker / Mode B CLI (preferred over keychain):
# CLAUDE_CODE_OAUTH_TOKEN=   # from `claude setup-token` on a trusted host
# ANTHROPIC_API_KEY=         # API-key fallback
# LLM_PROVIDER=claude-cli    # Mode A: graph/chat LLM = local claude subprocess
```

## Plugin Credential Vault

Plugin API keys (Tavily, Brave, Jules, job-search providers, etc.) can be stored in the **encrypted Postgres vault** (`VaultService` / `plugin_credentials` table, pgcrypto) instead of plain-text `.env` values. Tools resolve keys **vault-first**, then fall back to environment variables.

### Prerequisites

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | Yes | Postgres connection string |
| `MASTER_KEY` | Yes | 32-byte base64 encryption key (same value the server uses) |
| Postgres | Yes | Running and reachable (`docker compose up -d postgres` or full stack) |

**Host vs Docker hostname:** `.env` typically uses `@postgres:5432` (Docker-internal). When running vault scripts **on the Windows host**, override `DATABASE_URL` to use `@localhost:5432` with the same user, password, and database name.

### Interactive TUI (recommended)

Rich terminal UI — pick a plugin, set/update/delete keys with hidden input.

**Inside Docker** (recommended; container `.env` already has the correct `DATABASE_URL`):

```powershell
docker exec -it whiskers-agent-server python /app/terminal/script/manage_credentials.py
```

**On Windows host** (requires local Python 3.11+ and `pip install -r requirements.txt`):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL = "postgresql+psycopg://USER:PASS@localhost:5432/DBNAME"
python terminal/script/manage_credentials.py
```

**TUI flow:**

1. Table lists all plugins with required/optional credential status.
2. Enter plugin number (e.g. `search_plugin`).
3. Select a key number to set, or `c` custom key / `d` delete / `b` back.
4. Paste the secret when prompted (input is hidden).
5. `q` — quit.

### One-shot CLI

```bash
python terminal/script/add_credentials.py -p <plugin_id> --key KEY_NAME --value "secret"
```

Docker example (Tavily for `search_plugin`):

```powershell
docker exec whiskers-agent-server python /app/terminal/script/add_credentials.py -p search_plugin --key TAVILY_API_KEY --value "tvly-your-key-here"
```

### Example: `search_plugin` (Tavily / Brave)

`search_plugin` declares optional either-or keys in `manifest.json`:

```json
"optional_credentials": ["TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY"]
```

- `web_search` prefers **Tavily**, falls back to **Brave** when Tavily is unset.
- Store at least one key via the TUI or one-shot CLI above.
- **No server restart** is needed — `web_search` reads the vault on each call.

### When to restart the server

| Change | Restart needed? |
|---|---|
| Plugin vault keys | **No** — resolved at call time |
| LLM provider / API keys used by cached LLM clients | **Yes** |
| `.env` values loaded at container startup | **Yes** — `docker compose restart whiskers-agent` |
| `CLAUDE_CODE_OAUTH_TOKEN` / GoapAgent env | **Yes** — restart server process |

## GoapAgent (graph MCP + headless CLI)

GoapAgent lives under `core_graph/goap_agent/`. It is an **MCP-only** surface (no REST routes): the same LangGraph/GOAP **node factories** used by `run_graph`, exposed as tools so an external client (or a headless CLI) can drive the graph **node-by-node**.

| | **`run_graph`** | **GoapAgent** |
|--|-----------------|---------------|
| Who owns the loop | Server (compiled LangGraph) | Caller (MCP client / CLI) |
| State | Postgres checkpointer (`session_id` / `thread_id`) | In-memory session store (lost on restart) |
| GOAP candidate pool | Yes | **No** — `GoapAgent_*` ops are denylisted |
| HTTP API | Playground stream, etc. | None |

### Modes

1. **Stepwise MCP (manual or outer agent)**  
   Call `GoapAgent_create_session` → `GoapAgent_turn_init` → `GoapAgent_triage` → chat or task nodes via wrappers or `GoapAgent_invoke_node`.

2. **Mode B — headless CLI orchestrator**  
   `GoapAgent_run_cli_agent` spawns `claude` (or a generic CLI). For **claude**, Whiskers Agent auto-injects:
   - package instructions: `core_graph/goap_agent/instructions/CLI_AGENT.md` (`--append-system-prompt-file`)
   - temp `--mcp-config` pointing at this server’s `/mcp` URL + Bearer
   - `--strict-mcp-config` by default  
   Disable with `inject_mcp=false` or `GOAP_AGENT_AUTO_MCP=0`.

3. **Mode A — CLI as graph LLM**  
   Set `LLM_PROVIDER=claude-cli`. Graph/chat completions run through `CliAgentChatModel` (subprocess). `claude-cli` / `anthropic` use **Voyage AI** embeddings by default (`VOYAGE_API_KEY`, model `voyage-4`) — never a silent OpenAI fallback.

### MCP tools (tag `GoapAgent`)

| Tool | Role |
|------|------|
| `GoapAgent_list_nodes` | List GRAPH_SPEC node names |
| `GoapAgent_list_cli_drivers` | Which CLI binaries are available |
| `GoapAgent_create_session` | Seed session + `user_message` |
| `GoapAgent_get_state` / `destroy_session` | Inspect / drop session |
| `GoapAgent_invoke_node` | Run one node by name |
| `GoapAgent_<node>` | Thin wrappers (`turn_init`, `triage`, `planner`, `builder`, `executor`, …) |
| `GoapAgent_submit_confirm` / `submit_clarify` | Headless human gates |
| `GoapAgent_run_cli_agent` | Spawn headless CLI (Mode B) |

**Typical chat walk**

```text
create_session → turn_init → triage → chat_node → get_state
```

**Typical task walk**

```text
create_session (optional force_execute)
→ turn_init → triage → decompose → embedder → planner
→ context_check → permission_gate → builder → executor / wait → validator
→ summary / goap_goal as needed
```

On `confirmation_needed` / `need_input`, stop and use `submit_confirm` / `submit_clarify`, then continue.

### Headless CLI process logs

Each CLI run (default **on**) writes artifacts under `logs/goap_agent/<run_id>/`:

| File | Contents |
|------|----------|
| `process.log` | Lifecycle timeline + streamed stdout/stderr (incl. Claude `--verbose`) |
| `events.jsonl` | Structured events |
| `stdout.txt` / `stderr.txt` | Full captures |
| `run.json` | Summary (status, duration, paths, cost meta) |
| `mcp_config.redacted.json` | Injected MCP config with Bearer redacted |

Paths are returned on the tool result as `log` (e.g. `log.process_log`). Lines are also mirrored to the `whiskers.goap_agent.cli` logger (admin `/api/logs` SSE).

### Environment (GoapAgent / CLI)

```env
# Mode B auth (Docker image installs Claude Code when INSTALL_CLAUDE_CODE=1)
CLAUDE_CODE_OAUTH_TOKEN=
# ANTHROPIC_API_KEY=

# Auto MCP + instructions for GoapAgent_run_cli_agent (claude)
#GOAP_AGENT_AUTO_MCP=1
#GOAP_AGENT_STRICT_MCP=1
#GOAP_AGENT_MCP_URL=              # default {MCP_SERVER_URL}/mcp
#GOAP_AGENT_MCP_TOKEN=            # static Bearer; else inherit caller / mint
#GOAP_AGENT_MCP_CONFIG=           # prebuilt JSON path (skip auto-write)
#GOAP_AGENT_INSTRUCTIONS_PATH=    # override instructions/CLI_AGENT.md

# Process logging
#GOAP_AGENT_LOG=1
#GOAP_AGENT_LOG_DIR=              # default …/logs/goap_agent
#GOAP_AGENT_LOG_STREAMS=1
#GOAP_AGENT_CLI_VERBOSE=1         # claude --verbose (internal steps on stderr)

# CLI binaries / limits
#CLAUDE_CLI_BINARY=claude
#CLI_AGENT_TIMEOUT_S=300
#CLI_AGENT_WORKDIR=
#CLI_AGENT_USER=whiskers-claude  # non-root for --dangerously-skip-permissions
#CLI_AGENT_DROP_PRIVS=1
```

### Docker notes

- Image installs **Node 22** + `@anthropic-ai/claude-code` when `INSTALL_CLAUDE_CODE=1` (default).
- Prefer `CLAUDE_CODE_OAUTH_TOKEN` for non-interactive auth (not `--bare` alone — bare skips OAuth).
- The MCP server process may run as **root**, but **CLI subprocesses drop to `whiskers-claude`** (`CLI_AGENT_USER`, created in the Dockerfile). That non-root euid allows `--dangerously-skip-permissions` for headless tool use. Set `CLI_AGENT_DROP_PRIVS=0` or `CLI_AGENT_USER=` to disable.
- Rebuild the image after pulling so the CLI drop-privs account exists: `docker compose build whiskers-agent && docker compose up -d whiskers-agent`.
- Inspect a run: `docker exec whiskers-agent-server ls -la /app/logs/goap_agent` (or your `GOAP_AGENT_LOG_DIR`).

### Package layout

```text
core_graph/goap_agent/
├── mcp_tools.py           # FastMCP GoapAgent_* registration
├── node_runner.py         # GRAPH_SPEC factory invoke
├── session_store.py       # In-memory sessions (TTL + cap)
├── state_codec.py         # seed / merge / public snapshot
├── cli_inject.py          # instructions + --mcp-config injection
├── cli_run_log.py         # per-run process logs
├── instructions/CLI_AGENT.md
├── cli/                   # claude / generic drivers + runner
└── llm/cli_chat_model.py  # Mode A CliAgentChatModel
```

Scopes contributed at import: `plugin:GoapAgent`, `group:GoapAgent:read`, `group:GoapAgent:write`.

## Running the Server

### MCP over stdio
```bash
python whiskers_agent_mcp.py
```

### MCP over HTTP
```bash
python whiskers_agent_mcp.py --transport http --host 0.0.0.0 --port 10000
```

### Interactive CLI Agent
```bash
python agent.py --mode direct
```

### GoapAgent (MCP tools)

After the server is up (HTTP or via tunnel), use any MCP client that can call tools:

1. `GoapAgent_list_cli_drivers` — confirm `claude` availability  
2. `GoapAgent_run_cli_agent` with a short prompt — Mode B smoke  
3. Or `GoapAgent_create_session` + node tools for a manual graph walk  

See [GoapAgent (graph MCP + headless CLI)](#goapagent-graph-mcp--headless-cli).

### VS Code MCP Setup
```json
"Whiskers Agent MCP": {
  "command": "python",
  "args": ["./whiskers_agent_mcp.py"],
  "env": {
    "DATABASE_URL": "postgresql://mcp:mcp@localhost:5432/mcp",
    "MCP_SERVER_URL": "http://localhost:10000"
  }
}
```

Add domain plugin env vars when using a plugin that requires them.

## Docker Deployment

```bash
docker compose up -d
```

Server: `http://localhost:10000`

```bash
docker compose logs -f whiskers-agent-server
docker compose down
docker compose build --no-cache
```

## Project Structure

> Full file map, guardrails, and skills: [CLAUDE.md](./CLAUDE.md).

```text
.
├── whiskers_agent_mcp.py    # Canonical FastMCP entry (stdio / HTTP)
├── whiskers_mcp.py          # Backward-compatibility entry shim
├── agent.py                 # CLI search + graph execution
├── .env_sample              # Example env — never copy to .env by hand
│
├── core/                    # Platform: bootstrap, plugin_loader, proxy, route_registry,
│                            #   scope_management, auth/api keys, memory, artifacts, telemetry, LLM
├── core_graph/              # LangGraph: node/, goap/, subgraphs/ (FlowSpec specialists),
│                            #   model_roles/, runtime/, harness/, agent_loop/, goap_agent/, worker/
├── db_layer/                # SQLAlchemy models, vault, pgvector, per-domain stores, plugin migrator
├── api/                     # REST/WS: admin, plugins, playground, catalog, analytics, artifacts
├── oauth/                   # L1 inbound OAuthService + L2 ExternalOAuthRelay
├── plugins/                 # Domain packages (MCPTools/, migrations/, tests/ per plugin)
├── utils/                   # Response pipeline, safe_api_call, theme_registry, MinIO helpers
├── config/                  # server / plugin / tools API / embedding JSON
├── migrations/              # Alembic core chain (versions/core/)
├── frontend/                # cat-admin-frontend (React + TanStack + shadcn)
├── terminal/                # setup TUI, vault credential scripts
├── scripts/                 # dev.py, migrate.py, run_tests.py, NotebookLM compilers
├── Tools/                   # tools_generator, migration_generator, openapi_pipeline, semantic_tools_generator
├── test/                    # Core unit + integration (plugin tests live under plugins/)
├── whiskers-vscode/         # VS Code extension (gated PTY, step-up auth)
├── claude-plugins/          # Claude Code marketplace (portfolio-gen, world-context)
├── goals/                   # achieve() persistence
├── graphify-out/            # Generated knowledge graph (gitignored / dockerignored)
└── .claude/                 # Agent skills
```

**Legacy note:** Top-level `MCPTools/` and `langgraph_flows/` were removed. MCP tools live under `plugins/<plugin_id>/MCPTools/`; LangGraph logic lives under `core_graph/`. The core server and database schemas are strictly domain-agnostic — legacy domain tables and pre-rebrand identifiers have been retired.

### Route conventions
- Session-gated: `/api/{name}/session_gated/...` · public admin: `/api/admin/public/*`
- Catalog: `GET /api/catalog/session_gated`, `GET …/openapi`, `POST …/execute`
- Rate limit: sliding window on `rate_limit.path_prefixes` (default `/mcp`, `/portfolio`)
- CORS: `MCP_CORS_ORIGINS` allowlist; wildcard only if `MCP_CORS_RELAXED` is set

## Development Utilities

```bash
./install.sh --yes                         # first-time (Windows: .\install.ps1 -Yes)
python terminal/script/setup.py doctor     # preflight
python scripts/dev.py                      # Docker + Postgres + MCP
python scripts/run_tests.py                # pytest in the whiskers-agent-server container
```

Generate new plugin tools from YAML (output path is per-plugin, not a root `MCPTools/` folder):

```bash
python Tools/tools_generator.py --config Tools/tools_config.example.yaml \
  --output plugins/my_plugin/MCPTools/new_tools.py --schema
```

Other generator modes: `--interactive`, `--example`.

First-time setup: `./install.sh --yes` (or `python terminal/script/setup.py env` then `all`). Never `cp .env_sample .env` — see [SetupGuide.md](./SetupGuide.md).

## Key Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | Yes (vault/OAuth/graph) | Postgres connection |
| `MASTER_KEY` | Yes (vault) | 32-byte base64 encryption key |
| `MCP_SERVER_URL` | No | Public server URL for OAuth discovery / GoapAgent MCP inject |
| `LLM_PROVIDER` | No | `openai`, `anthropic`, `gemini`, `gemini-vertex`, `claude-cli` |
| `OPENAI_API_KEY` | No | When `LLM_PROVIDER=openai` |
| `VOYAGE_API_KEY` | No | Default embeddings for `anthropic` / `claude-cli` (`voyage-4`) |
| `CLAUDE_CODE_OAUTH_TOKEN` | No | Headless Claude Code auth (Docker / Mode B / Mode A) |
| `GOAP_AGENT_AUTO_MCP` | No | Default on — inject MCP config for `GoapAgent_run_cli_agent` |
| `GOAP_AGENT_LOG` | No | Default on — write `logs/goap_agent/<run_id>/` process logs |
| `PLUGIN_API_URL` | No | Optional base URL for dynamic HTTP routes |

Full sample (including GoapAgent CLI knobs): [`.env_sample`](./.env_sample). Contributor detail: [CLAUDE.md](./CLAUDE.md).

## Tech Stack

- **Core**: Python 3.11, FastMCP, `rich` TUIs, `jsonschema` (catalog execute)
- **Orchestration**: LangGraph (Postgres checkpointer), custom GOAP planner, FlowSpec specialists
- **Data**: PostgreSQL + pgvector, SQLAlchemy 2.0 async, Alembic (core) + plugin SQL migrator, MinIO
- **Security**: PyJWT RS256, bcrypt, argon2-cffi, pyotp, pgcrypto vault
- **Frontend**: React, TanStack Router/Query, Zustand, shadcn, Vitest, Playwright

## Notes

- Core orchestration is domain-agnostic; domain integrations live in `plugins/`.
- Prefer **`run_graph`** for one-shot multi-step goals; use **GoapAgent** when you need stepwise control or a headless CLI orchestrator.
- Blocking HTTP from `async def` goes through `utils.api_utils.safe_api_call` (and `asyncio.to_thread` when the call is sync).
- Tests run in Docker (`python scripts/run_tests.py` → `whiskers-agent-server`) unless you are in a git worktree.
- **Never** `docker compose down -v` — that wipes `postgres_data` (API keys, OAuth keypairs, vault). Use `docker compose down` or `docker restart whiskers-agent-server`.
- `MASTER_KEY` must be byte-identical across restarts. Do not copy `.env_sample` → `.env` (placeholder key will encrypt the keypair under `your-key`).
- Contributor architecture, guardrails, and the skills map: [CLAUDE.md](./CLAUDE.md).
