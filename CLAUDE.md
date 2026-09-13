# Whiskers Agent MCP Server — Project Index

Whiskers Agent — Plugin-driven Model Context Protocol platform. FastMCP over stdio + HTTP, LangGraph/GOAP
orchestration, two-layer OAuth, encrypted credential vault, hot-swappable plugins, React admin console.
The core server is domain-agnostic; domain integrations live in `plugins/`.

- **GitHub**: https://github.com/CooLguNxDD/OpenCat-Mcp-Full
- **Notion**: https://www.notion.so/MCP-Project-3352783caabd801d9a05ecdf106def00
- **Guardrails detail**: [.claude/skills/core.md](./.claude/skills/core.md)
- **Setup**: [SetupGuide.md](./SetupGuide.md) · [ReadMe.md](./ReadMe.md) · [CONTRIBUTING.md](./CONTRIBUTING.md)
- **Code Review Ignore**: generated `graphify-out/` (gitignored and dockerignored)
- **test**: test should run on docker container (`whiskers-agent-server`; compose service `whiskers-agent`) unless it is a worktree
---

## 1. Architecture Guardrails (non-negotiable)

| Rule | Detail |
| --- | --- |
| **Tenant isolation** | Multi-tenant data access **requires** a `tenant_id` filter. Never let it default to `None` — that drops the `WHERE` clause and leaks across tenants. Public portfolio surfaces pin `PORTFOLIO_TENANT_ID = 1`. |
| **SSRF prevention** | Validate every external/proxy URL with `core.proxy.ssrf_safety._is_safe_url`. Async requests go through `core.proxy.proxy_manager._safe_async_client` (`_SSRFSafeTransport`) so redirects can't TOCTOU past the check. |
| **DB abstraction** | No raw `text(...)` SQL in HTTP route handlers. Encapsulate in `db_layer/*_store.py` or a service. |
| **Scope enforcement** | All access gates route through `core.scope_management.evaluate_access` / `ScopeManager`. Never re-implement `is_allowed` inline (`test_scope_import_boundary.py` enforces this). `core/api_key_management/scopes.py` is a back-compat shim only. Read `.claude/skills/scope-management/SKILL.md` before touching scope/auth. |
| **Scope vocabulary** | `core/scope_management/registration.py` (`PermissionRegistry`) is the single source of truth. Plugins register via manifest `"scopes"` or `ctx.contribute_scopes(...)` (merges, never replaces). Every plugin manifest must declare a top-level `"scopes"` block — `test_plugin_manifest_scopes.py` fails CI on one that doesn't. A plugin that ships without one falls back to a synthesized floor (`plugin:<id>`, `group:<id>:read`/`write`, `PluginLifecycleManager` in `lifecycle_manager.py`) so it stays reachable rather than silently admin-only, and `PermissionRegistry.mark_synthetic`/`run_boot_scope_health` flag it as `scopes_synthetic` at boot — treat that warning as a bug to fix (declare real scopes), not a steady state. Three-level grammar (`grammar.py`): level-1 `core:<domain>:<access>` (seeded `defaults/core_scopes.json`), level-2 `plugin:<id>[:access]` / `group:<id>:<tag>` / `op:<id>:<operation_id>`, level-3 plugin gate ceiling (`gates.py`, manifest `"gate"` block, `plugin_gate_ceiling` rule). HTTP endpoints enforce `required_scopes` via `SessionGateMiddleware._check_required_scopes` when non-empty (bound across `api.config`, `api.analytics`, `api.apikeys`, `api.route`, `api.proxy`, `api.plugins`; `api.admin` has no SESSION_GATED routes to bind — all `PUBLIC`). |
| **Worker lifecycle** | Background workers self-register into `WorkerRegistry` (`core_graph/worker/worker_registry.py`). Never instantiate loops directly, and never hand-roll `_run_loop`/`run`/`stop` — build on `BackgroundWorker` (`core_graph/worker/worker_loop.py`), which owns the stop event, the idle wait and the cancel-and-await. A tick returning `True` means "did work" (loop again); `False`/`None` idles. `register()` is idempotent (`replace=True` for hot-reload); query via `get`/`is_registered`/`is_running`/`names`, never `_workers`. `register_all()` is **core-only** — plugins self-register in `on_load`/`on_ready` and stop in `on_unload`, importing `core_graph.worker.worker_loop` / `.worker_registry` by module path (the package `__init__` eagerly pulls in SQLAlchemy). `core_graph/worker/checkpoint_sweeper.py` sweeps stale `ephemeral-` LangGraph threads every `scheduled_jobs.checkpoint_sweep.interval_seconds` (default 1h) as a backstop for `evict_ephemeral_thread`'s abort/restart gap. `core_graph/worker/artifact_sweeper.py` does the same for `artifact_links` + their MinIO objects (`scheduled_jobs.artifact_sweep`: `interval_seconds` 1h, `retention_hours` 720, `buckets` allowlist default `[ARTIFACT_BUCKET]`) — the allowlist is what keeps portfolio render assets and job_search resume PDFs, which share the table but not the lifetime, out of an ephemeral-blob retention window; a tick is skipped whole when `minio_available()` is false, and a row whose object delete hard-fails is left for the next tick so a link never outlives its object. |
| **Non-blocking** | Blocking sync calls inside `async def` must be offloaded with `asyncio.to_thread` and wrapped with `utils.api_utils.safe_api_call`. Fire-and-forget tasks (no caller awaits the result) go through `utils.tasks.spawn_supervised` instead of bare `asyncio.create_task` — it logs an unretrieved exception (rather than the silent "Task exception was never retrieved") and keeps a strong reference so the task can't be GC'd mid-flight. |
| **Bounded concurrency** | Cap unbounded fan-out with `asyncio.Semaphore` (e.g. `Semaphore(10)` for embedding batches); GOAP fan-out uses `FANOUT_CONCURRENCY`. |
| **Catalog identity** | Live ops resolve by `(plugin_id, operation_id)` only. `route_embeddings` is a *search index*, not the live catalog. Never trust FE-only schema validation on execute. See skill `inference-guide`. |
| **Typed events** | EventBus payloads use Pydantic models, not untyped dicts. |
| **Plugin ownership** | Plugin-owned models/stores/migrations/tests live under `plugins/<pkg>/`, never in core `db_layer/` or top-level `test/`. |
| **No plugin→plugin imports** | A plugin never imports a sibling. Dispatch by identity through `core.route_registry.execute.execute_operation(plugin_id, operation_id, args, caller_scopes=...)` (reference: `portfolio_plugin/discovery/sources.py::invoke_proxy`); if the target is a plain store function, expose it as a catalog op first. Share plain values via the service locator (`PluginContext.register_service` / `get_service`, backed by `PluginRegistry`) and declare the dependency in the manifest's `"requires"` so the loader's toposort can order it. |
| **No plugin→core-internal imports** | A plugin never imports `core.artifact_store.minio_client`, `db_layer.artifact_link_store`, `db_layer.api_key_store`, `core.api_key_management.*`, or reaches `oauth_provider._svc`/`OAuthService._mint_jwt`. Storage goes through `ctx.artifact_store` / `core.artifact_store.get_artifact_store()` (`IArtifactStore`, `core/interfaces/artifact_store.py`); inbound auth (bearer/session-cookie → principal, scoped-token minting) goes through `ctx.auth_service` / `core.auth_service.get_auth_service()` (`IAuthService`, `core/interfaces/auth_service.py`). Most call sites (route handlers, `@mcp.tool()` functions, pre-accept WS handshakes) never receive a `PluginContext`, so the module-level `get_artifact_store()`/`get_auth_service()` singletons are the primary entrypoint, not the `ctx.*` properties. `test/unit/test_plugin_core_import_boundary.py` fails CI on any bypass (module-level or function-local). |

---

## 2. Dev Rules

1. Update `CLAUDE.md` + the relevant skill after every change.
2. New pattern → add to the skill set under `.claude/skills/`; new files/dirs → add to §5; new deps → add to §8.
3. Scaffold tool modules with `Tools/tools_generator.py`; plugin DDL with `Tools/migration_generator.py`.
4. **Never commit unstaged changes.** Stage per step only.
5. **Relative paths only** — `Path(__file__).parent` or repo-relative. Never hardcode `C:\`.
6. Docs: top-level JSDoc on every component/hook/store slice; 1–3 line docstring on every exported function; 1-line inline comments for non-obvious logic.
7. **Python/tests run in Docker** (`whiskers-agent-server`, `-e PYTHONPATH=/app`). Always use `python scripts/run_tests.py` (auto-delegates host→container).
8. `.dockerignore` must keep heavy dirs out of build context (`.venv/`, `frontend/`, `**/node_modules/`, `.claude/`, `logs/`, `pgadmin/`, …).
9. PR/commit titles: `["Component":"Feature"] Description` (see CONTRIBUTING.md).
10. Before planning an implementation, pull component context from NotebookLM (skill `notebooklm`); after a major stage, recompile NotebookLM bundles (`scripts/compile_notebooklm_skills.py`, `scripts/compile_notebooklm_tunnel.py`).

---

## 3. Runtime Topology

### 3.1 Entrypoints
- `whiskers_agent_mcp.py` — Canonical FastMCP startup/teardown, middleware stack, route + tool registration.
- `whiskers_mcp.py` — Backward-compatibility shim delegating to `whiskers_agent_mcp.py`.
- `agent.py` — CLI search & graph execution (`--dangerously-skip-permissions` bypasses permission gates).
- `core/bootstrap/` — phased boot (`orchestrator.py`, `phases.py`, `registry.py`): keypair guard, plugin discovery, registry reconciliation, scope pre-seed + health.

### 3.2 Middleware order (`core/context/_app.py`)
`ResponseShapeMiddleware` → `ArtifactOffloadMiddleware` → `ResponseLimitingMiddleware`.
Shaping first so offload sees shaped output; offload before limiting so oversized payloads get a
MinIO short-id marker instead of destructive byte truncation. `RateLimitMiddleware` mounts before
CORS in `whiskers_agent_mcp.py` / `whiskers_mcp.py` when `rate_limit.enabled`.

### 3.3 Graph (`core_graph/`)
Multi-stack entry via `core_graph/runtime/mode_router.py` → `oneshot_cli` | `root`
(`GRAPH_MODE=auto|oneshot|root`). Topology under `core_graph/subgraphs/`
(`root`, `triage`, `classic_goap`, `specialist`, `oneshot_cli`) registered in `SubgraphRegistry`;
plugins contribute domain agents via `PluginContext.contribute_subgraph`.

`core_graph/states.py::DynamicAPIState` is composed by **TypedDict multiple inheritance** from
four sub-states (`TriageSubState`, `PlanExecutionSubState`, `WorkflowSubState`, `AuditSubState`) —
typing-only organization, flat at runtime (a TypedDict is a plain `dict`; `StateGraph(DynamicAPIState)`,
the checkpointer, and every node's flat `state.get(...)` access are unaffected). Only `messages`
(`add_messages`) and `model_audit` (`operator.add`) carry a reducer; introspect the schema via
`typing.get_type_hints(DynamicAPIState, include_extras=True)`, never bare `DynamicAPIState.__annotations__`
(inherited-key merging behavior differs across Python versions) and never a bare `get_type_hints`
without `include_extras=True` (silently strips both reducers).

**Specialist FlowSpec framework** (`core_graph/subgraphs/specialist/`): specialists are declared as
data, not hardcoded Python. A plugin ships `flow_specs/*.json` (`FlowSpec`: `claims` predicate,
ordered `stages`, `requires`/`provides` GOAP facts) linked from `manifest.json`
(`settings.specialist_agent.flow_specs`, paths relative to the plugin dir, `..`/absolute rejected);
the loader (`core/plugin_loader/lifecycle_manager.py::_load_flow_specs`) parses + registers them into
`flow_registry`. `specialist_entry` calls `flow_registry.select_flow(goal, goal_class)` before the
legacy LIFO domain-runner list and the generic `run_specialist_pipeline` fallback — adopting flows is
additive. Each stage is either `kind: agentic` (one `agent_loop.run_agent` call over `tool_globs`) or
`kind: deterministic` (one `execute_operation` dispatch); `flow_runner.run_flow` enforces
`required_outputs`, `on_fail` (`fail_closed|continue|retry:N`), and `repeat_until` stage jump-back.
Every registered flow is also published into `OperationCatalog` as a synthetic
`specialist/<flow_id>` op (`flow_registry._flow_to_operation`), so a **GOAP plan step can dispatch a
specialist flow** the same way it dispatches any other operation — this is the substack: GOAP keeps
the deterministic skeleton, a flow absorbs the nondeterministic middle. `PluginContext.contribute_flow_spec`
covers flows that must be built at runtime instead of declared as JSON. **Fixed 2026-08-13**: the
manifest-flow/effort/model-role loading call sites in `seed_runtime_from_spec` referenced an
undefined `LifecycleManager` name (class is `PluginLifecycleManager`) — silently swallowed by the
method's broad `except Exception`, so `flow_specs`/`specialist_agent.effort`/`model_roles` manifest
keys never actually loaded via the plugin lifecycle path outside of tests that register flows
directly. Now fixed; those keys are live.

**Model-role layer** (`core_graph/model_roles/`): model choice per pipeline role/agent call is
declarative data — a `ModelRoleSpec` (frozen dataclass, strict fail-closed parser, mirrors `FlowSpec`
exactly) resolved against the existing `llm_pool.strength` column via
`core/llm_config_service.py::resolve_step_llm_config`. `role_spec.py` (schema: ordered `ladder` of
`Rung(selector)`, declared `Validation`, pre-emptive `entry_conditions`, reactive
`escalate_on_exception`/`escalate_on_invalid`, `terminal_fallback`) → `registry.py`
(`ModelRoleRegistry`, precedence **DB override > core default > plugin**; a plugin cannot shadow a
core role_id) → `resolver.py` (`compile_selector`: alias/`effort:<level>`/pool-name/numeric → hint
string, 5s-TTL `PoolSnapshot`, invalidated by `invalidate_graph()` + every pool mutation) →
`ladder.py::run_role_ladder` (the single executor every node calls instead of hand-coding a cascade;
degrades to one `fallback_llm` attempt when the role is absent or `MODEL_ROLES_ENABLED` is off,
default **off**). Cut over: `triage`, `chat`, `decompose`, `planner_goal`, `planner_linear`,
`goal_verifier`, `summary`, `reranker` (now returns `(candidates, token_usage)`) — every ladder ships
`[{"selector": "core"}]` so behaviour is unchanged until an operator declares real rungs.
`state["model_audit"]` (`Annotated[list, operator.add]` — must be a reducer since `parallel_groups`
runs concurrently) records which rung fired and why.

**Specialist stack + manifest effort** (`core_graph/model_roles/selection.py::select_agent_model`):
the FlowSpec stage/flow/manifest/subgraph resolution chain for agentic calls, in precedence order —
`StageSpec.model` > `StageSpec.effort` > `FlowSpec.effort` > manifest
`specialist_agent.effort_overrides[goal_class]` > manifest `specialist_agent.effort` >
`SubgraphSpec.model_role`/`effort` > core role `"specialist"`. Manifest effort is loaded by
`PluginLifecycleManager._load_specialist_effort` into `model_roles/manifest_effort.py`'s registry
(fail-closed: unknown effort level → logged + not registered, never coerced). `AgentSpec.llm_kind` —
previously dead, any non-`"core"` value routed to `get_graph_core_llm()` regardless — is now a real
selector via `AgentSpec.model` (`core_graph/agent_loop/runner.py::_get_llm`); resolves exactly one
rung, never a ladder (the agent loop is multi-step/stateful — a retry would re-run side-effecting
tool calls). Example: `plugins/portfolio_plugin/manifest.json`'s `specialist_agent.effort: "high"` +
`effort_overrides.discover: "low"`.

**Model-role config surface** (operator-facing, S7): `core_graph/model_roles/node_bindings.py::NODE_ROLES`
declares which graph node resolves which role_id(s) — the single source of truth consumed by both the
frontend generator and a test (`test_node_role_bindings.py` regex-scans real `run_role_ladder`/
`llm_for_role` call sites and fails CI if they drift from the map). DB overrides for per-role ladders
*and* the `effort_map` live under one `server_settings['model_role_specs']` key
(`db_layer/model_role_store.py`, strict validation on write via `parse_model_role_spec`/`ModelRoleSpecError`
→ 400, lenient skip-and-log on read) — a separate key from `step_model_policy` since that store's
`_apply_patch` accepts unvalidated complex values, exactly what the model-role parser exists to reject.
`core_graph/model_roles/db_overlay.py::apply_db_overrides()` loads the store into
`ModelRoleRegistry.set_db_override`/`resolver.set_effort_map_override`; called once at boot
(`core/bootstrap/phases.py::phase_6_registry_sync`) and again after every write in the three
`api/config_routes.py` routes (`GET/POST config/model-roles`, `DELETE config/model-roles/{role_id}`) so
a save is visible on the very next request rather than waiting on the 5s pool-snapshot TTL that
`invalidate_graph()` (called alongside) still busts. `scripts/gen_graph_topology.py` now also emits
`frontend/.../components/goap/modelRoles.gen.ts` (role_id union, effort/alias vocabulary, `NODE_ROLES`
typed against the sibling `graphTopology.gen.ts`'s `NodeId` — a node-id typo is a TS compile error, not
silent drift) alongside the existing `graphTopology.gen.ts`. Admin console: Config → **Model Roles**
(`ModelRoleSection.tsx`) — per-role ladder editor with a live resolved-model preview per rung, plus the
fleet-wide effort_map editor. Playground: `core_graph/states.py`'s `model_audit` reducer already streams
through `stream_graph_impl`'s state snapshot; `components/goap/modelAudit.ts::auditByNode` groups it by
node with zero backend change, rendered as a model-tier chip on `GraphCanvas` nodes (amber + `⇡` when
`escalated`) and a full per-rung/per-attempt breakdown in the inspector's Model block. TUI:
`terminal/tui/setup_tui.py::_screen_model_roles` (Live Settings → 4) covers ladder-as-CSV editing,
effort_map, and reset-to-default; full Validation/entry_conditions editing stays admin-UI-only.

Node chain (`core_graph/node/`):
`turn_init → triage → decompose → embedder → planner → context_check → step_resolver →
permission_gate → builder → executor/wait → validator → step_dispatcher → round_summary →
goap_goal → {continue → planning_entry | done → summary}`.

Key behaviors:
- **Hybrid GOAP planner** (`core_graph/goap/`): LLM extracts goal + seed facts, deterministic A* search
  orders actions and derives `arg_bindings`; falls back to a linear LLM planner on miss.
- **Goal loop** (`goap/goal_loop.py` + `node/goap_goal.py`): goal as first-class GOAP facts
  (`did:` / `have:`), bounded by `MAX_ITERATIONS` (default 20) plus two stall-breakers
  (repeated-terminal-failure, no-progress). `research_notes` accumulate per round.
- **round_summary** — deterministic, no LLM: MinIO artifact offload, bounded note append, memory fold.
  **summary_node** — final-only single LLM call, synthesizes against the never-mutated `original_query`;
  writes the user-facing prose back onto `messages` as an `AIMessage` (same as `chat_node`) so the
  next turn's planner/search sees the answer, not only the questions. Planner/decompose scratch
  AIMessages are tagged `additional_kwargs.internal` and omitted from `format_context_block`.
  `turn_init` emits `RemoveMessage` for all but the last 30 checkpointed messages.
- **Multi-model + parallelism**: `assign_step_models` (policy in `server_settings['step_model_policy']`),
  `derive_parallel_groups` → concurrent `asyncio.gather` in `step_dispatcher_node` with partial-failure
  collection (`failed_steps`).
- **Fan-out**: count-aware multi-fetch (`detect_fanout_count`, `for_each`/`for_each_values`,
  `pending_fetch_ids`), bounded by `FANOUT_CONCURRENCY` / `MAX_FANOUT`.
- **Sessions**: LangGraph Postgres checkpointer keyed `thread_id = session_id`; per-session locks;
  `shutdown_checkpointer` on teardown. The `messages` reducer (`add_messages`) holds both
  `HumanMessage` (incoming) and user-facing `AIMessage` (chat reply / final summary). History
  projection is `core_graph/prompts/context_block.py::history_from_messages` (roles `user`/
  `assistant`/`system`, internals filtered, per-entry ~800 char cap, last 12 turns).
  A bare lookup imperative (`search up!`, `look it up`) is not a `query` seed —
  `resolve_search_query` substitutes the prior topical user turn (or `last_summary`).
  `apply_followup_search_topic` only replaces missing/empty/imperative search seeds;
  a topical LLM seed (e.g. `Mika Misono Blue Archive`) is kept.
- **Agent loop** (`core_graph/agent_loop/`): generic `AgentSpec`/`ToolRef` tool-calling runner over the
  live `OperationCatalog`. Bind read-only tool globs narrowly — never blanket `*`.
- **Harness** (`core_graph/harness/`): global instructions from `server_settings['harness_instructions']`
  + RAG recipes/anti-patterns on `memory_content_vectors` + plugin skills.
- **GoapAgent** (`core_graph/goap_agent/`): stepwise MCP node surface + headless CLI drivers
  (`claude`/`agy`/`grok`) with auto MCP-config + instruction injection, privilege drop to
  `whiskers-claude`, run logs under `logs/goap_agent/<run_id>/`. Recursion guard via
  `ocat_source=goap_agent_cli` token claim.

### 3.4 Response pipeline (`utils/response_shape.py`, `response_format.py`)
Async-only 11 steps: normalize → offload_minio → strip_* → dedupe → project → limit → include_meta → format.
Flat return contract: `apply_shape_async` returns bare list/dict unless `include_meta: true`.
`core_graph/node/execute_step.py::_unwrap_discovery_envelope` is the GOAP boundary that flattens
`{_meta,data}` envelopes. `run_graph` output guarded by `_sanitize_envelope` / `_compress_carrier`
(CSV compression before the truncation sentinel; sacred fields never touched).

`_response_shape` **hint text is core-owned** (`utils/response_shape_hints.py`), rendered from the
pipeline's own vocabulary (`utils.response_shape._PIPELINE_MAP` keys, `utils.response_format.ResponseFormat`
members) so it can't drift the way the old prose did. Injected unconditionally into the FastMCP server
instructions at `core/context/_app.py` construction time — present with **zero plugins loaded**, not
delivered by a plugin's `config.json` (the pre-2026-08 `shape_hint`/`format_hint` blocks in
plugin `config.json` files are gone; a plugin
may still ship those keys as a per-tool-description *append* only — `core/config_loader.get_response_hints`
merges core text first). Operator override: `config/tools_api_config.json` top-level `"hints"` block
(`{"shape_hint": [...], "format_hint": [...]}`). `core.route_registry.execute.execute_operation` now pops
and honours a caller-supplied `_response_shape` (v1: opt-in only — no shape passed → byte-identical raw
result, so existing GOAP/FlowSpec deterministic-stage callers are unaffected), gated by the same live
`graph.direct_call_shaping` kill switch and `graph.direct_call_shaping_exclude` list as
`core.context.response_shape_middleware`. An op whose `input_schema` already declares `_response_shape`
keeps the key and is not externally re-shaped.

---

## 4. Subsystems

| Subsystem | Location | Notes |
| --- | --- | --- |
| Plugin loader | `core/plugin_loader/` | Two-pass async discovery, toposort deps, hot-swap, event-bus lifecycle (`tools.enable`, `routes.contribute`, …), content-hash staleness, skills/poll_specs/scopes/config registries. `types.py` holds `IPlugin`/`IPluginContext` (`typing.Protocol`) + `PluginManifest` (`TypedDict`) — no runtime imports of sibling loader modules, mirrors `core/interfaces/`'s convention. `Plugin.on_ready` (`plugin.py`) reads `ctx._registry` directly rather than the global `get_registry()` singleton, which is what keeps `plugin.py` free of any import on `plugin_registry.py` (a real `plugin_registry` → `plugin` → `plugin_registry` cycle would otherwise fire, since `plugin_registry.py` imports the concrete `Plugin` class at module level for the re-export every `plugin_config.py` relies on). `plugin_lifecycle_registry.py` types `register_plugin`/`_plugins`/`_plugin_id_map` against `IPlugin` instead of the concrete class. `test_plugin_loader_import_cycles.py` AST-scans the package for module-level cycles on every run. |
| Scope management | `core/scope_management/` | `ScopeManager`, ordered rules (incl. `plugin_gate_ceiling`), `PrincipalKind`, sentinels `all`/`*`, policy `enforce|audit|off`, three-level grammar (`grammar.py`), plugin gates (`gates.py`/`gate_overlay.py`), legacy-token compat (`legacy_map.py`), C01–C20 contracts. |
| Route registry / catalog | `core/route_registry/` | `OperationDescriptor` + `OperationCatalog` (revision/etag, scope-aware filter), host HTTP mirror, `execute.py` (jsonschema-validated), OpenAPI export. |
| Proxy | `core/proxy/`, `core/proxy_tools/` | Mount lifecycle, SSRF-safe transport, scope token registration, gateway tool-visibility (hide all but `run_graph`/`discover_tools`/`authenticate`/`complete_authentication`). |
| Auth | `oauth/`, `core/api_key_management/`, `core/user_management/` | Layer 1 inbound RS256 JWT + PKCE; Layer 2 per-plugin external OAuth relay; pgcrypto API keys (`[]`=deny-all, `["all"]`=bypass). `core/auth_service.py::get_auth_service()` is the plugin-facing boundary (`IAuthService`) — `principal_from_bearer`/`principal_from_session_cookie`/`mint_scoped_token` wrap `core.context._oauth_svc` + `core.api_key_management.store` so plugins never touch `oauth_provider._svc` or `OAuthService._mint_jwt` directly; mounted on `PluginContext.auth_service`. |
| Memory | `core/memory/` | Tenant-scoped namespaces; backends `memory` → `memory_content_vectors`, `search` → `search_content_vectors`. `memory_plugin` is an MCP façade only. |
| Search engine | `db_layer/embeddings/search_engine.py` | Single choke point: `SearchSpec` + `search()` decides hybrid (dense cosine + `ts_rank_cd` FTS + RRF) vs dense-only per collection. All 6 search paths are thin adapters. |
| Artifacts | `core/artifact_store/` | MinIO offload, tenant-scoped `short_id` links, session-gated REST, MCP `list/get/fetch_artifact` (GOAP-denylisted). `plugin_store.py::get_artifact_store()` is the plugin-facing boundary (`IArtifactStore`) — wraps `minio_client` + `db_layer.artifact_link_store` behind `put_bytes`/`get_bytes`/`presigned_url`/`create_link`/`link_by_short_id`/`short_id_exists`; mounted on `PluginContext.artifact_store`. Retention: `artifact_sweeper` (see §1) deletes row + object past `scheduled_jobs.artifact_sweep.retention_hours`, bucket-allowlisted. |
| Telemetry | `core/telemetry/`, `db_layer/telemetry_store.py`, `db_layer/analytics_store.py` | Event buffer persistence + real-time WS streaming, analytics KPIs. Core **never imports a plugin for a metric**: plugins push live gauges in from their lifecycle hooks via `collector.register_gauge_provider(key, callable)` / `unregister_gauge_provider` (e.g. relay's `session_registry.active_count` in `on_ready`; `api/analytics_routes.py`'s `active_sessions` KPI reads this too — never `plugins.*` directly). `snapshot()` evaluates each provider in its own try/except, so one bad provider can't zero the rest; an unregistered gauge falls back to `0`. **Product axis** (`core_049_telemetry_feature_and_graph_runs`): `tool_call_events.feature` (`'mcp'` default) vs `graph_run_events` (one row per whole graph run, `collector.record_graph_run`) — a tool call dispatched *inside* a run still records to `tool_call_events` with `parent_run_id` and must never be summed as a second graph row (real invocation path: `core_graph/mcp_tool.py::_stream_graph_impl_inner`'s `finally`, not `mode_router._run_root`, which the current stack-selection wiring never reaches for a root-classified request — kept for direct/test callers only). Plugin-owned ask audit: `portfolio_ask_turns` (`plugins/portfolio_plugin/ask/telemetry.py`, mirrors bake's `record_bake_run` dual-write) — overlay content stays ephemeral, only question/intent/outcome persist. Visitor-controlled string columns are `VARCHAR` capped (`0008_ask_turn_column_widths`; `create_ask_turn` clips). TTL sweepers (`telemetry_ttl_sweeper`, plugin `ask/ttl_sweeper`) delete at most 5000 rows per tick and return `True` so a backlog drains without one unbounded `DELETE`. A failed collector flush requeues the failed batch *in front* of events that arrived mid-await so `maxlen` drops oldest. |
| LLM providers | `core/llm_provider_management/` | Dynamic `ProviderSpec` registry — one file per provider, no if/elif dispatch. Adding a provider = new file + one import. |
| Migrations | `migrations/versions/core/` (Alembic, core only) + `plugins/<pkg>/migrations/` (`NNNN_name.sql|py`, applied by `db_layer/plugin_schema_migrator.py`) | Plugin DDL never uses Alembic branches. |

### LLM providers (built-in)
`openai`, `anthropic`, `gemini` (AI Studio), `gemini-vertex` (Express mode, API-key only),
`voyage` (embeddings only), plus CLI-subprocess providers `claude-cli`, `agy-cli`, `grok-cli`.
Defaults: anthropic/claude-cli chat → `claude-sonnet-5`; gemini → `gemini-3.1-flash-lite`.
Anthropic/claude-cli embeddings → Voyage `voyage-4` @ 1024 dims (**not** an OpenAI fallback) —
set `VOYAGE_API_KEY` and **re-embed** legacy 1536-dim rows; no automated backfill.

---

## 5. Project Structure

```
whiskers_mcp.py          FastMCP entrypoint (middleware, routes, tools)
agent.py                CLI search + graph execution
core/                   platform: bootstrap, context, plugin_loader, proxy, proxy_tools,
                        route_registry, scope_management, api_key_management, user_management,
                        memory, artifact_store, telemetry, clustering, llm, llm_provider_management,
                        dynamic_tools, interfaces
                        (portfolio_plugin/ask/ holds the visitor-ask patch path — see §6)
core_graph/             LangGraph orchestrator: node/, goap/, subgraphs/ (specialist/ holds the
                        spec+MCP-driven FlowSpec framework: flow_spec.py, flow_registry.py,
                        flow_runner.py, blackboard.py — see §3.3), model_roles/ (spec-driven
                        multi-model role selection: role_spec.py, registry.py, resolver.py,
                        ladder.py, selection.py, manifest_effort.py, defaults/core_roles.json —
                        see §3.3), runtime/, harness/, agent_loop/, goap_agent/, prompts/, worker/,
                        registry/, mcp_tool.py, states.py
db_layer/               connection, models/, embeddings/, vault, per-domain *_store.py
                        (incl. model_role_store.py — model_role_specs DB overrides),
                        plugin_schema_migrator.py
api/                    REST/WS routes: admin, plugin_routes/, tool, config, playground, api_key,
                        analytics, health, catalog, artifact, relay, log, proxy, route,
                        middleware.py, rate_limit_middleware.py
oauth/                  OAuthService (L1), ExternalOAuthRelay (L2), provider + routes
plugins/                portfolio_plugin, job_search_plugin
                        (posting_ingest.py, portfolio_link.py, flow_specs/career_ops_apply_v1.json),
                        search_plugin, memory_plugin, jules_plugin, cat_terminal_relay_plugin,
                        world_semantic_plugin
utils/                  response_shape/response_format (11-step pipeline), api_utils, short_id,
                        config_registry, server_config, error_response, minio_client, telemetry,
                        theme_registry (JSON palettes → hex for SVG/TUI; SUPPORTED_THEMES)
config/                 server_config.json, plugin_config.json, tools_api_config.json,
                        embedding_config.json (+ *_example.json)
migrations/             Alembic core chain (versions/core/)
Tools/                  tools_generator.py, semantic_tools_generator.py, migration_generator.py,
                        openapi_pipeline/ (live OpenAPI / FastAPI / Flask / Express ingest)
terminal/               setup TUIs + scripts (setup.py, setup_tui.py, ui.py, manage_credentials.py);
                        Catppuccin rich Theme in tui/theme.py (`WHISKERS_TUI_THEME`)
scripts/                dev.py, migrate.py, reseed.py, run_tests.py, gen_graph_topology.py,
                        compile_notebooklm_*.py, upsert_all_*.py
test/                   unit/ (~200 modules), integration/ (route + persistence suites)
frontend/               cat-admin-frontend (React admin console), console
whiskers-vscode/      VS Code extension (gated PTY, step-up auth)
claude-plugins/         Claude Code marketplace: portfolio-gen, world-context
goals/                  agent goal files / achieve() persistence
.claude/                skills/, docs/ (context.md + references)
```

### Route conventions
- Gated: `/api/{name}/session_gated/...`; public admin: `/api/admin/public/*`.
- Path grammar built by `core/route_registry/path_builder.py`.
- Catalog REST: `GET /api/catalog/session_gated`, `GET …/openapi`, `POST …/execute`. List/OpenAPI ETag is the **filtered view** (scopes + `plugin_id` + `slot`), not `catalog.etag`; `Cache-Control: no-store` on 200 and 304. Console `useCatalogQuery` treats 304 as reuse-this-query or throw — never an empty/`getCatalogOperations()` snapshot. `CatalogClient.call`/`op` refresh-once on a snapshot miss (no blind `/execute`).
- Rate limiting: sliding window per IP on `rate_limit.path_prefixes` (default `/mcp`, `/portfolio`) → 429 + `Retry-After`; in-memory, not cross-worker.
- CORS: `MCP_CORS_ORIGINS` allowlist; relaxed wildcard mode is an **explicit opt-in** via `MCP_CORS_RELAXED` (`utils/server_config.py::resolve_cors_policy`).
- Admin console: session-gated queries wait for `/api/admin/public/me` (`useAuthedQueryEnabled`); TOTP enrollment honors `?returnTo=/terminal`; playground execute is SchemaForm + `catalogClient` (`resolveCatalogOp`). **Tunnel Analytics** (`/analytics?tab=&range=`) is a controller + four tab views (`OverviewTab` / `ToolsTrafficTab` / `AskTurnsTab` / `ErrorsHealthTab`) over shadcn `ChartContainer` + Recharts 3 (`McpGraphChart` dual Y-axis, `PluginStackedChart` stacked area); live KPIs merge in `overlay.ts`, buckets flatten in `series.ts`.

---

## 6. Featured Plugins

- **portfolio_plugin** — the largest plugin. Discovery (`discovery/`: proxy-first repo inventory →
  normalize → semantic index → reconcile, agentic-first with deterministic fallback), compose
  (`compose/`: floor composer, block builder, scoped/custom layout, DAG banding, context enrich,
  fish tank), layout engine (`layout/`: LayoutPlan IR, recipes, jury), agents (`agents/`: discovery,
  composer, layout, bake), schema mirror (`schema/ui_layout_schema.py` — **keep in sync with
  CatPortfolio `src/content/schema.ts`**; every `BLOCK_TYPES` member needs a `_DAG_LEVEL_BY_TYPE` band,
  guarded by `test_portfolio_composer.py`). Theme vocabulary is the JSON glob via
  `utils.theme_registry` (`themes.py` re-exports `SUPPORTED_THEMES`) — jury, design-context
  (`theme_defs` + ids), SVG motifs, and the TUI all read that registry. A new theme is a
  `*.theme.json` file; do not add Python/TS id lists. Job "bake & send": pre-tailored layout + `?j=<short_id>`
  URL baked into the resume PDF; public read route `GET /api/portfolio/public/layout/{job_id}`.
  Operator write tools are GOAP-denylisted; only compose tools stay GOAP-visible.
  Bake quality (`bake/contract.py`): `assess_bake_quality` is the **single** contract, used
  identically by the MCP tool and `run_bake_agent` — schema validity, silently-dropped blocks,
  block count/type diversity, DAG band coverage, grounding, jury threshold, and `ship_best`
  (jury never passed). Gate runs *after* tailor + fishTank, closing the unvalidated-persist hole.
  `portfolio_layout.hard_fail_on_quality` (default **false**, soak first) makes a failing bake
  persist **nothing** — no row, no short_id — while still writing a run record; CatPortfolio 404s
  to the bundled master layout. `compose.recipes.layout_quality_ok` is now a shim over the
  structural subset only. Never re-add a second quality bar in `agents/bake.py`.
  Bake module layout (`bake/`): `MCPTools/bake_tools.py` is the **tool surface** only —
  `job_signals.py` (widen the JD via catalog dispatch), `compose_flow.py` (the fail-open compose
  ladder + `_stamp_compose_meta`), `persist.py` (variadic `allocate_short_id`), `contract.py` (the
  single quality bar) and `run_context.py` hold the pipeline; `MCPTools/bake_admin_tools.py` holds
  the operator-only `list_bake_runs`. Tests patch each helper **where it now lives**, not on
  `bake_tools`.
  Bake observability (`bake/run_context.py` — run-context accumulation, deliberately named apart
  from the quality `bake/contract.py`): `BakeContext` accumulates per-stage errors + timings across the
  fail-open compose ladder, so `errors[]` carries a real cause instead of a placeholder; every
  attempt writes a `portfolio_bake_runs` row (`short_id` NULL when nothing shipped) plus
  `collector.record_tool_call` — including the empty-resolve early return (no JD / posting).
  Operator MCP `list_bake_runs` is the debug surface (GOAP-denylisted; principal tenant only). `compose_path`/`mode`/`degraded`/`plan_json` are promoted to
  queryable columns on `portfolio_job_layouts`. Both render modes read one layout — `fishTank` is
  a block, not a second bake (`settings.fish_tank_enabled`, default **true**). The base `?tank=1`
  snapshot and ask-mode overlays build the tank from **all active** `portfolio_projects` (not just
  agent-scoped cards): operator-kept rows with links/context_sources pass even when the summary is
  still a thin GitHub placeholder (manual inventory is not required to exist on Notion). A
  **job-scoped bake** (`bake_portfolio_for_job`) additionally trims that roster to JD-relevant
  projects via `compose/job_tailor.py::rank_projects_for_tank` (manifest
  `settings.fish_tank.job_roster_limit`/`min_relevance`, default 8/2) — no project is exempt by tag,
  so a `primary`-tagged case study still has to score against the JD; a JD with zero relevance signal
  falls back to the full inventory rather than an empty tank. `portfolio_projects.summary` is
  **planner context only** — fish blurbs, card bodies, and timeline lines are authored by
  `compose/display_copy.author_display_copy` (name/tags/metrics + JD-scored claims). Agent card
  bodies that dump inventory are rewritten at bake (`rewrite_layout_project_copy`); FlowSpec stage
  `author_display` precomputes `display_copy_by_slug`. Caps live in manifest
  `settings.display_copy` (`fish_blurb_max_chars` default 200). Never paste/truncate inventory
  summary into GenUI display fields. `job_tailor` / bake stamp `meta.highlightSlugs` from the same
  JD scores that order the cards, so tank curation and text order can't diverge.
  Project timeline (`0006_project_timeline`): `portfolio_projects.started_on`/`ended_on`/
  `timeline_source` (`notion_period`|`github`|`manual`) — `ended_on IS NULL` means ongoing,
  not unknown. `discovery/period.py::parse_period` extracts a Notion "Period Covered" line
  before `compose/quality.py` strips it as README chrome for display copy; GitHub repos fall
  back to `created_at`/`pushed_at` (only when stale >6mo, else ongoing). `reconcile.py` prefers
  `notion_period` over `github` and never overwrites a `manual` (operator-set) date. `fish.py`
  depth is chronological (newest = shallow) whenever the tank has >=2 dated projects, with
  `sort_order` as the fallback for undated ones; each fish gets `startYear`/`endYear` (omitted,
  never null) and the tank gets `timeSpan` — CatPortfolio's `src/content/schema.ts` mirror must
  add the same optional fields for the client to render a swim band instead of a fixed depth.
  **Ask mode** (`ask/`, FlowSpec `portfolio_ask_v1`, manifest `settings.ask`, default **on**):
  a visitor question patches within the 1–3 block `settings.ask.max_patch_blocks` budget
  instead of rebuilding the page; an emitted `fishTank` block counts against that same budget.
  `router.py::route_ask` is deterministic (no LLM) — it reuses `job_tokens` /
  `rank_projects_by_query` / `search_context` / `matched_project_slugs` and emits one intent:
  `bake` (hand off to `portfolio_bake_v1`) · `focus_fish` · `add_fish` · `discover` ·
  `patch_blocks` · `answer_only` · `recommend`. A client `add_slugs` list short-circuits
  to `add_fish` after inventory/fish-pool validation (never trust a client slug into the
  roster; cap `MAX_BATCH_ADD_FISH = 4`). Focus is the best **lexical** inventory hit, not the
  bake-ranker's first row: visitor glue (`the`/`about`/`tell`/`project`) is dropped and
  slug/name/tag hits outweigh a long summary. `job_tokens` keeps two-letter domain words
  (`ai`, `ml`, …) with word-boundary scoring. A text-mode match that is not already in
  the tank also patches the `fishTank` block so switching `?v=tank` shows the fish. `targets.py` addresses blocks by **(type, slug)** so
  agent-invented ids in existing `?j=` rows need no migration, and `SACRED_BLOCK_TYPES`
  (hero/kpiGrid/statStrip/quickActions) can only be rewritten by a real bake.
  `overlay.py::build_ask_overlay` returns **changed blocks only** plus a recomputed dag
  (`merge_dag_bands` needs just `(id, type)` pairs, so the client ships a block *index*, not a
  layout) and a `recommendations` array of rich dicts (`slug`/`name`/`blurb`/`tags`/`reason`/
  `in_tank`) on every status branch — CatPortfolio renders `💡 Ask about …` vs `+ Add … to tank`
  chips from `in_tank`. `ask/fish_pool.py` stashes not-in-tank recs under the visitor session at
  route time so an add-chip turn can `take_from_session` without re-ranking. Nothing is persisted
  — the overlay is ephemeral and dies on reload.
  `status: "error"` (fetch behind the patch failed, e.g. `list_projects` throws) is distinct
  from `status: "ok"` + empty `blocks` (a legitimate answer-only turn) — a DB failure must never
  be silently coerced into "nothing to patch". `ask/contract.py::assess_patch_quality` is a
  separate bar from `assess_bake_quality` (a 2-block patch fails the whole-page one for reasons
  that don't apply) and `hard_fail_on_quality` does not reach it. `discovery_jobs.py` forces
  `dry_run=True` / `write_back=False` so public traffic can never mutate `portfolio_projects`;
  its job table evicts on TTL/cap with real task `.cancel()` (no orphaned background discovery),
  and a job in `error`/`empty` is dropped rather than rejoined so a re-ask gets a fresh attempt
  instead of the same stale miss for the rest of the TTL.
  A `discover` turn now **waits** on the job (bounded by `ask.discovery_budget_s`, default 20s)
  and, when ready, spawns a `"discovered"`-tagged virtual fish via `ask/spawn.py` into the
  existing tank block (`extra_projects` on `_build_tank_block`) — still no bake, no new `?j=`,
  no `portfolio_projects` row. `do_index=True` with the explicit `index_on_dry_run=True` opt-in
  (`ask.discovery_index`) is the one public-traffic write and it targets
  `portfolio_plugin__context` only, so a re-ask of the same topic can resolve through
  `_virtual_projects_for_query` without a second sweep.
  CatPortfolio Ask wraps the visitor sentence in a `run_graph` assistant prompt.
  `ask/visitor_turn.py` unwraps it: triage pins **specialist** (no LLM),
  `specialist_entry` seeds the FlowSpec board with the visitor question +
  `block_index`/`tank_slugs`/`dag`/`time_span`, and `goal_class` is
  `classify_specialist_goal(question)` — the wrapper's hardcoded `scoped_ask`
  must not steal a bake.   Overlay fields (`blocks`, `focus_slug`, `highlight_slugs`, `pending_job`,
  `recommendations`) and `answer_markdown` are hoisted onto the flow envelope so the SPA can merge
  without reading `carry`. Discovery keeps priority; when it comes back empty/error the overlay
  falls through to those recommendations instead of a bare miss. `portfolio_ask_v1`'s agentic `answer` stage is
  `on_fail: continue`; `ensure_ask_answer` then guarantees visitor markdown
  (replacing the `Flow '…' completed` debug line). `compose/quality.py`
  strips HTML comments before `_truncate_at_boundary` so leftover `<!--` cannot
  surface as a trailing `<!` on cards.
  The clamp that actually holds is the FlowSpec's `answer` stage `tool_globs`
  (`search_portfolio_context` + `get_project_context` only) — the ask tools are
  GOAP-denylisted and `scoped_ask` is deliberately **not** in `agentic_goal_classes`, since
  that flag routes to `run_layout_agent`, i.e. the full rebuild. Prompt-steered patching was
  tried first (`patchDirective`) and did not hold.
  Two mitigations make block-grain tank patching safe: `build_fish_tank_block(time_span=…)`
  freezes the chronological depth scale so one added dated project can't re-depth the school,
  and CatPortfolio's `FishTankCanvas` keys its scene effect on **roster** identity
  (`rosterKey`) rather than the `fish` array, so unchanged specimens keep locomotion across a
  block swap. `build_floor_layout(include_fish_tank=…)` / `GET …/public/layout?tank=1` are
  opt-in — the plain public snapshot stays text-only (WebGL-free).
  **Ask-mode scoping**: the public CatPortfolio site authenticates with an API key scoped to
  `group:portfolio_plugin:ask` only (declared in `manifest.json`'s `"scopes"`, alongside
  `plugin:portfolio_plugin`/`read`/`write`) — enough for `route_portfolio_ask` /
  `build_ask_overlay` and, because `search_portfolio_context` / `get_project_context` also
  carry the `ask` tag, the `answer` stage's grounding too. It is denied every write-tagged
  operator op. Discovery/spawn need no scope of their own: `discovery/sources.py::invoke_proxy`
  dispatches with `caller_scopes=None` (trusted-local), so the whole spawn chain is gated
  solely by `build_ask_overlay`'s own check. FlowSpec dispatch resolves scopes via
  `core_graph/runtime/caller_scopes.py::resolve_caller_scopes` — `state["caller_scopes"]`
  (the validated MCP token, seeded by `mode_router`) first, then the `get_request_principal()`
  contextvar (playground REST only), then stdio-unrestricted, then fail-closed for anonymous
  HTTP. `PortfolioAgent_run` is deliberately **not** in `GATEWAY_ALWAYS_VISIBLE`
  (`utils/server_config.py`) — allowlisting it would let any authenticated caller, including
  the ask key, reach the full portfolio agent with zero scope check.
- **job_search_plugin** — search / enrich / evaluate / apply / track tool groups; MinIO-stored resume
  and cover-letter PDFs; `JobApplication.portfolio_job_id` traces the baked portfolio layout.
  Paste ingest + board identity live in `posting_ingest.py` (Indeed `jk`/`vjk`, agency/via/
  `end_employer`, `employment_class`); recruiter-safe portfolio URLs in `portfolio_link.py`
  (never `localhost:11000`). **`career_ops_apply_v1`** FlowSpec (`flow_specs/career_ops_apply_v1.json`) pairs this plugin with
  `portfolio_plugin` into one dispatchable pipeline — ten deterministic stages: ingest →
  signal-resolve → liveness → fit-score → bake → tailor resume/cover letter → render PDF → track.
  Liveness is its own stage, not something `fetch_job_posting` does inline: that tool is tagged
  `read`/`readOnlyHint: True` while a liveness check writes a `job_posting_liveness` row. It sits
  after `signals` because a stage's `reads` entry *is* the callee's parameter name verbatim — an
  op reading a slot the callee has no parameter for dies in `execute_operation` and `on_fail:
  "continue"` swallows it into a silent no-op, so `resolve_pipeline_signals` re-emits
  `posting_url`/`role` as `url`/`role_title` for `check_job_liveness`. Built for a
  CLI agent (Claude Code or similar) driving the external Career-Ops tool over the tunnel — see
  skill `career-ops-pipeline`. **Call the MCP tool `run_career_ops_pipeline(goal,
  applicant_profile_id, url|raw_text, theme)` directly** — do not rely on `run_graph` natural-language
  routing to reach it. The flow is also registered as a synthetic catalog op
  (`execute_operation("specialist", "career_ops_apply_v1", {...})`, same mechanism as
  `portfolio_bake_v1`/`portfolio_ask_v1`) and *claims* apply-flavored goals in `specialist_entry`,
  but getting there depends on `core_graph/prompts/triage_prompt.py`'s LLM classifying the turn as
  `"specialist"` first, and that classification is probabilistic — verified live, the same goal
  phrasing sometimes falls through to the generic linear planner instead, which improvises a wrong
  partial subset of the pipeline's tools. `run_career_ops_pipeline` (in `MCPTools/pipeline_tools.py`)
  bypasses triage entirely: it resolves caller scopes straight from the MCP access token
  (`core_graph.mcp_tool._caller_scopes`) and dispatches `flow_runner.run_flow` directly — no
  generic "execute this catalog op" MCP tool exists on the gateway, so this direct wrapper is the
  only reliable external entrypoint. `triage_prompt.py`'s specialist bullet list and
  `triage.py::_PORTFOLIO_HINTS`' heuristic fallback were both extended to also recognize
  apply-pipeline goals — improves natural-language routing odds but does not make it deterministic;
  prefer the direct tool. Two new read-only adapter ops in `MCPTools/pipeline_tools.py`
  (`resolve_pipeline_signals`, `prepare_application_record`) exist solely to bridge blackboard slot
  names between existing tools that were never designed to chain (`flow_runner`'s deterministic
  stages pass blackboard keys as kwargs verbatim, with no rename) — `bake_portfolio_for_job`'s
  `short_id` and `tailor_cover_letter`'s `tailored_text` also gained additive alias keys
  (`portfolio_job_id`, `cover_letter_text`) for the same reason. Signals also re-emit
  `url`/`role_title` and fill `company` from `via` when `end_employer` is unnamed.
  `prepare_application_record` omits a portfolio URL when bake failed or the public
  host is localhost. The flow always writes
  `sync_application_status`'s lifecycle value as `"drafted"` — it prepares an application, never
  submits one.
- **search_plugin** — `web_search` (Tavily → Brave → keyless DuckDuckGo) + `fetch_url`;
  `semantic_index` / `semantic_search` over `search_content_vectors`.
- **cat_terminal_relay_plugin** — terminal relay with step-up auth (TOTP/password), `ElevationService`,
  `command_guard` binary allowlist, native headless `exec_command` light sandbox. The sandbox spawn
  (`MCPTools/sandbox_tools.py`) never inherits the server's `os.environ` — it passes an explicit
  minimal `env` (`PATH`/`HOME`/`LANG`/`TERM`) — and runs `start_new_session=True` so a timeout kills
  the whole process group (`os.killpg`, SIGTERM→grace→SIGKILL, always `await proc.wait()`), not just
  the `/bin/sh` wrapper. stdout/stderr are read via bounded stream readers capped at
  `SANDBOX_MAX_OUTPUT_BYTES` — never `proc.communicate()`, which buffers unboundedly before the cap
  can apply. `routes/control_routes.py::_sandbox_elevate_api_key_subject` resolves the caller via
  `core.auth_service.get_auth_service()` and gates on `core.scope_management.evaluate_access`
  (not a hand-rolled `allowed & scopes` check) — this means it now also honors
  `scope_management.policy`'s enforcement switch: under `scope_policy=off` any valid API key
  passes this gate, same as every other `evaluate_access` call site.
- **jules_plugin** — Jules cloud-agent sessions + review fleet; poll_specs drive wait-step injection.
- **world_semantic_plugin** — Unity hex-world spatial context (index/diff HTTP + MCP query tools).

---

## 7. Essential Commands

```bash
# First-time setup (one command; Docker daemon must be running)
./install.sh --yes                 # uv venv + requirements + setup.py all -y
# Windows: .\install.ps1 -Yes    ·  make install  ·  make install-dev
# Never copy .env_sample → .env (placeholder MASTER_KEY will brick the keypair)

python terminal/script/setup.py            # full guided setup; ends in the setup TUI
python terminal/script/setup.py doctor     # preflight checks
python terminal/script/setup.py env|llm|db|migrate|plugin_migrate|admin|plugins|up|health|tui
python terminal/script/setup.py all -y --from db   # resume; --force to redo
make setup                                 # Unix equivalent
terminal\setup.bat                         # Windows wrapper (uses .venv when present)

# Dev stack (Docker + Postgres + pgAdmin + MCP)
python scripts/dev.py

# Setup / credentials TUI (vault-encrypted plugin keys)
python terminal/script/setup_tui.py        # or: python -m terminal
python terminal/script/manage_credentials.py
python terminal/script/add_credentials.py -p <plugin_id> --key KEY --value VAL

# Database
python scripts/migrate.py
python scripts/reseed.py --full            # WARNING: --full also clears seeded data

# Tests
python scripts/run_tests.py                            # pytest in Docker (testpaths: test/ + plugins/)
npm --prefix frontend/cat-admin-frontend run test:unit
npm --prefix frontend/cat-admin-frontend run test:e2e

# Lint / typecheck  (pip install -r requirements-dev.txt)
ruff check .
mypy api core_graph --ignore-missing-imports
make lint && make typecheck
```

**CI**: `.github/workflows/ci.yml` — ruff (warn mode + bare-except ratchet on changed files),
mypy scoped to `api/` + `core_graph/` (warn mode), unit tests against a `pgvector/pgvector` service DB.
Config in `pyproject.toml` and `requirements-dev.txt`. Pullfrog agent workflows under `.github/workflows/pullfrog*.yml`.

---

## 8. Tech Stack

- **Core**: Python 3.11, FastMCP, `rich` (terminal TUIs), `jsonschema` (catalog execute validation)
- **Orchestration**: LangGraph (Postgres checkpointer), custom GOAP planner
- **Data**: PostgreSQL + pgvector, SQLAlchemy 2.0 async ORM, Alembic, MinIO (presigned object storage)
- **Security**: PyJWT (RS256), bcrypt, argon2-cffi, pyotp (TOTP step-up), pgcrypto vault
- **Frontend**: React + TanStack Router/Query, Zustand, shadcn, Vitest, Playwright

---

## 9. Environment & Persistence Safety

| Variable | Required | Default |
| --- | --- | --- |
| `WHISKERS_API_URL` | yes | — |
| `MASTER_KEY` | yes (DB) | 32-byte base64 |
| `DATABASE_URL` | no | `postgresql://mcp:mcp@db:5432/mcp` |
| `LLM_PROVIDER` | no | `openai` |
| `OPENAI_API_KEY` | conditional | required when `LLM_PROVIDER=openai` |

API keys, sessions, and keypairs live in Postgres (`api_keys`, `auth_keypairs`, `oauth_tokens`),
encrypted under `MASTER_KEY` via pgcrypto.

- **Never run `docker compose down -v`** — that deletes the `postgres_data` volume and wipes all keys.
  Use `docker restart whiskers-agent-server` or plain `docker compose down`.
- Keep the compose project name and checkout directory stable — the volume name derives from them;
  renaming produces a fresh empty DB that looks like "keys vanished".
- `MASTER_KEY` must be byte-identical across restarts/deploys. Drift silently breaks decrypt in
  `_load_private_key`; `phase_keypair_guard` (`core/bootstrap/`) fails the container loudly at boot.
- Revoke (`POST /api/auth/api-keys/{key_id}/revoke`) rotates: revokes and returns a fresh replacement token.
- `scripts/reseed.py` without `--full` intentionally leaves `api_keys` / `auth_keypairs` alone.

---

## 10. Skills Reference (`.claude/skills/`)

| Skill | Topic |
| --- | --- |
| `core.md` | Architecture guardrails (read first) |
| `mcp-server-setup` | Bootstrap, transport, module registration |
| `plugin-system` | Lifecycle, discovery, registries |
| `scope-management` | **Mandatory** before any scope/auth edit |
| `oauth-two-layer` / `oauth-handler` | L1/L2 auth, vault, PKCE |
| `inference-guide` | Operation catalog, host mirror, `callCatalogOp`, SchemaForm |
| `langgraph-chain` / `achieve-pipeline` | Graph nodes, states, prompts; `achieve()` GoalSpec |
| `http-route-registry` | Route declaration + hot reload |
| `tools-builder-generator` / `openapi-pipeline` / `dynamic-tools-plugin` | Tool authoring & generation (`openapi-pipeline`: live spec or FastAPI/Flask/Express source → OpenAPI → MCP tools) |
| `Alembic-migration` / `sqlAlchemy-orm-query-builder` | Schema + async ORM patterns |
| `llm-provider-enum` | Provider registry, thread-safe lazy init |
| `error-handling-wrapper` / `logger-convention` | `safe_api_call`, `whiskers` logger namespace |
| `analytics-telemetry` / `export-report` | Metrics, persistence, async export pipeline |
| `pytest-docker` | Test execution in Docker |
| `playwright-mcp-validation` | Browser-only validation passes (admin console, CatPortfolio), step template + soak |
| `react-app-guide` / `react_generator` | Frontend architecture + scaffolding |
| `diff-review` / `merge-request` / `architecture-audit` | Review, PR flow, audit |
| `agy-tdd-pipeline` / `notion-writeup` | TDD orchestration, Notion publishing |
| `career-ops-pipeline` | One-call `career_ops_apply_v1` flow pairing job_search_plugin + portfolio_plugin for a CLI agent driving Career-Ops |
| `theme-registry` | Shared JSON theme vocabulary (`utils.theme_registry`, `isLight`, water tokens, CatPortfolio `gen:themes`) |

Plugin-local skills live under `plugins/<pkg>/skills/` and are injected into planner prompts
(e.g. portfolio `layout-design-builder.md`, `context-discovery.md`, `agentic-layout-composition.md`;
jules `jules-sessions/SKILL.md`; job_search `job-search-pipeline/JOB_SEARCH_SKILL.md`).

## 11. Claude Code Plugins (`claude-plugins/`)

Marketplace `whiskers-oct` (`.claude-plugin/marketplace.json`):
`claude plugin marketplace add <path>` then `claude plugin install portfolio-gen@whiskers-oct`.

- **portfolio-gen** — `/portfolio-gen "<brief>"`, `/portfolio-verify`; skills for layout authoring,
  block authoring, portfolio PRs, context sourcing. Consumed headless by CatPortfolio's
  `.github/workflows/portfolio-gen.yml`.
- **world-context** — Unity ↔ `world_semantic_plugin` routing; `/world-index`, `/world-query`.
