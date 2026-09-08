# Whiskers Agent Core Architecture & Guardrails

## System Capabilities

### Core Routing & Orchestration
- **Entrypoints**: The backend is orchestrated via `whiskers_agent_mcp.py` (`whiskers_mcp.py` shim) for FastMCP startup/teardown and `agent.py` for CLI search and graph execution.
- **LangGraph Dynamic Orchestrator (`core_graph/`)**: Implements an extended hybrid GOAP flow: `turn_init` -> `triage` -> `embedder` -> `planner` -> `context_check` (confirm/clarify) -> `step_resolver` -> `permission_gate` -> `builder` -> `executor` -> `validator` -> `step_dispatcher` -> `goap_goal` -> `summary`.
- **API & Plugins**: Plugins and components dynamically surface capabilities to the system. Plugin discovery is powered by the `/api/plugins` endpoint.
- **Proxy Infrastructure**: Consolidated proxy infrastructure (`core/proxy/`) handles requests external to the system or third-party resources.

## Architecture Guardrails

### Multi-Tenant Isolation & Security
- **Strict Tenant Enforcements**: In multi-tenant data access operations (e.g., `core/user_management/store.py`), a `tenant_id` filter is strictly required. Never allow `tenant_id` to default to `None`, as this bypasses `WHERE` clauses and leads to cross-tenant data leakage.
- **SSRF Prevention**: All external or proxy URLs must be validated using the `_is_safe_url` asynchronous helper from `core.proxy.ssrf_safety` before processing. Use `core.proxy.proxy_manager._safe_async_client` (which uses `_SSRFSafeTransport`) for asynchronous HTTP requests to securely handle redirects and prevent Time-of-Check-to-Time-of-Use (TOCTOU) vulnerabilities.
- **API Key Scopes**: Scopes use string tokens (e.g., `plugin:<id>`). `null` grants legacy full access, `[]` denies all, and non-empty lists grant exact permissions.
- **Authentication**: JWT extra claims must include `ocat_role`, `ocat_user_id`, and `ocat_tenant`.

### State Management & Layer Boundaries
- **Database Abstraction**: Database operations using raw SQL (e.g., `text(...)`) must be encapsulated within the data access layer (such as repositories or services like `EmbeddingService`). Never place raw DB operations directly inside HTTP route handlers.
- **Event Handlers**: Do not rely on transient external states or untyped dictionaries. Use typed Pydantic models for EventBus payloads.

## Performance Paradigms

### Async/Await & Non-Blocking Rules
- **Synchronous Offloading**: Blocking synchronous HTTP requests or operations (e.g., `requests.request`, interactive `rich.prompt.Prompt.ask`) inside `async def` functions must be offloaded to threads using `asyncio.to_thread` and wrapped with `utils.api_utils.safe_api_call` to prevent event loop blocking.
- **Concurrency & Rate Limits**: Asynchronous operations that hit rate limits (e.g., document embeddings) must use concurrency control mechanisms like `asyncio.Semaphore` (e.g., `asyncio.Semaphore(10)` in `aembed_documents`).

### Lifecycle Management & Worker Registries
- **Resource Cleanup**: Explicit database connection shutdown hooks (e.g., `shutdown_checkpointer` for closing LangGraph memory state pools) must be invoked to handle safe connection teardown.
- **Background Workers**: Managed strictly using the `WorkerRegistry` pattern (`core_graph/worker/worker_registry.py`). Workers self-register via `WorkerSpec(run, stop)`. External components must use generic start/stop handlers rather than directly instantiating loop processes.
- **Bounded Loops**: The `goap_goal` node bounds autonomous loops (`MAX_ITERATIONS`), and parallel executions fan out using level-based topological groupings bounded by `FANOUT_CONCURRENCY`.
