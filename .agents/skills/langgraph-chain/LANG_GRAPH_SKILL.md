---
name: langgraph-chain
description: 'CODE PATTERN SKILL — LangGraph agent graph for multi-step tool orchestration. USE FOR: interactive vs headless modes, system prompts, state management, graph compilation, routing nodes. DO NOT USE FOR: LLM provider enum (use llm-provider-enum skill), individual tool authoring (use tools-builder-generator skill).'
---

# LangGraph Chain Execution

## Files

| File / Folder | Role |
|---|---|
| `core_graph/mcp_tool.py` | `run_graph` MCP tool — thin entry; mode_router selects oneshot vs root |
| `core_graph/runtime/` | Bootstrap (checkpointer/graph), mode_router, stack registry, retriage |
| `core_graph/subgraphs/` | Topology ownership: `triage`, `classic_goap`, `oneshot_cli`, `specialist`, `root` |
| `core_graph/graph.py` | `build_dynamic_graph()` — compiles ROOT_SPEC |
| `core_graph/states.py` | `DynamicAPIState`, `ExecutionStep`, `RouteCandidate` TypedDicts |
| `core_graph/prompts/` | Prompt constants (e.g. planner, builder, triage) and GOAP agent prompts / helpers |
| `core_graph/goap/` | Classical A* GOAP engine (classic stack only) |
| `test/unit/test_goap_chain.py` | GOAP collection mapping / chaining tests |

### Multi-stack entry (2026-07)

```
run_graph → mode_router
  ├─ oneshot_cli (CLI core provider + GRAPH_MODE auto/oneshot)
  └─ ROOT graph: turn_init → triage → chat | classic | specialist
                                      │              │
                                      └── retriage (cap) ──┘
```

| Mode | Meaning |
|------|---------|
| `chat` | `chat_node` |
| `classic` | GOAP plan→exec→goal (`task` legacy alias) |
| `specialist` | `specialist_entry` portfolio pipeline / `PortfolioAgent_run` |

Env: `GRAPH_MODE=auto|oneshot|root`, `CLI_PROVIDER_ONESHOT=0` disables oneshot.

---

## Dynamic LangGraph Orchestrator Flow

The Whiskers Agent agent orchestrates natural-language requests into dynamic multi-step plans:

```
decompose → embedder → planner → [gate] → context_check ──(unresolved?)──► step_resolver ──(response?)──► END
                                    │            │                             │
                              confirm_node       └───────(all resolved)────────┼──► builder → executor → validator → step_dispatcher
                                    │                                          │
                              clarify_node → END                               └─► (no response) ─┘
```

**Decompose-first pipeline** (`graph.decompose_first` config flag, default ON): `decompose_node`
(`core_graph/node/decompose.py`, `DECOMPOSE_PROMPT`) runs one small LLM pass BEFORE retrieval,
splitting the request into ≤`max_subtasks` (default 4) single-action `sub_tasks` plus preliminary
`decompose_seed_values`, grounded by a TTL-cached `build_tools_summary()` catalog block. The
`embedder` then runs one pgvector search **per sub-task** (intent precedence: `sub_tasks` →
`goal` list → `user_query`), merging into a deduped pool capped at `candidate_pool_max`
(default 30; pre-existing `candidate_pool` entries are never evicted mid-loop). The planner
receives the sub-task block + preliminary seeds as prompt hints and merges
`decompose_seed_values` **under** its own candidate-aware `seed_values` (planner wins on
conflict). All replan loops — clarify `replan`, goal-loop `continue`, validator `replan` —
re-enter at `decompose` so failures re-decompose with `replan_context`/`remaining_goal_facts`.
On any LLM/parse failure decompose degrades to `sub_tasks=[user_query]` and never gates; the
planner's confidence gate stays authoritative. Output goes to dedicated state keys
(`sub_tasks`, `decompose_intent`, `decompose_seed_values`) — **never** to `goal`, whose
truthiness flips agentic-mode gates. Edge targets are resolved by
`core_graph/graph_spec.py` (`planning_entry()`/`replan_entry()`, used as flag-resolved
`ConditionalEdge` mapping targets in `GRAPH_SPEC`); flag OFF restores the legacy
embedder-first wiring exactly (the decompose node is not even registered).

### Confidence Gate
Decides how to proceed based on a weighted confidence score:
`weighted score = 0.6 * pgvector_score + 0.4 * llm_confidence`

* **`> 0.70` (execute)** → Instantly proceed to `builder` and `executor`
* **`0.50 - 0.70` (confirm)** → Route to `confirm_node` to get user approval before running
* **`< 0.50` (clarify)** → Route to `clarify_node` asking the user to rephrase

### Safety & Invariants
1. **Long-Chain Override**: Plans with $>3$ steps auto-route to `confirm` regardless of the confidence score, allowing the user to review before execution. If the compiled plan is not yet available, `confirm_node` iterates and displays the planner's raw `instruction_set` instead of showing an empty plan.
2. **Recoverable Errors**: If the executor receives a `400` or `422` HTTP status, the `validator` automatically retries once.
3. **Parallel Execution**: Steps that have no unsatisfied dependencies are grouped together in `parallel_groups` and executed concurrently via `asyncio.gather`. The LLM YAML planner may emit groups directly; the **GOAP path derives them** with `derive_parallel_groups(steps)` (`core_graph/goap/integrate.py`) from each step's `depends_on` + `$steps[i]` arg_bindings via level-based topological grouping (fan-out/`wait` steps stay singletons), gated by the live `step_model_policy.parallel_enabled` flag.
   - **Per-step model assignment**: `assign_step_models(steps, pool_entries, policy)` stamps `step["model"]` (a pool entry name) per a configurable policy (`off`/`strength`/`task_type`/`explicit`) from `server_settings['step_model_policy']` (`db_layer/step_model_settings_store.py`, REST `/api/config/step-models`, UI `StepModelSection`). Runs on the GOAP path (`planner_node`) and the linear path (`builder_node`); resolved at execution via `resolve_step_llm()`. The playground inspector (`PlannerOut`) shows a per-step model badge + `‖ grp N` parallel-group badge.
4. **Resilient JSON Parsing**: To prevent type errors on modern LLMs (e.g. Gemini, Anthropic) that can return message contents as list/block structured formats, `_parse_json_response` converts input dynamically, extracting text from standard strings, dictionary blocks (`"text"` key), or objects with a `.text` attribute before parsing JSON or executing regex search.
5. **Replan Response Clearance**: The `planner_node` resets `"response"` to `None` in the state when recalculating a plan, ensuring that `gate_router` does not prematurely route to `done` on the replanned workflow.
6. **Enforced Required Parameter Validation**: The `builder_node` always validates required route parameters and prompts the user via `need_input` if any are missing, even if `force_execute` is enabled. It relies on the upstream `step_resolver` (the per-step LLM sub-agent resolver) to resolve missing required parameters in agentic mode, force confirmation on write operations, or yield a `need_input`/confirmation response beforehand. The validation in `builder_node` acts as a thin safety net for retry paths.
7. **Context-Aware Dynamic Replanning**: When a step yields an empty result that a downstream step depends on, or a recoverable/transient error is encountered, the `validator` triggers a replan reset (clearing the current plan, workflow ID, and resetting `"response"` to status `"replan"`). A structured failure record is logged in `replan_context` and `last_failure`. This failure memory is formatted and injected into the LLM goal prompt and route selection prompt to adapt search queries or select alternative tools. The budget is governed by `MAX_REPLANS` (defaulting to 2).
8. **GOAP seed literal passing to proxy fast-paths**: The goal-extraction LLM emits `seed_values` (exact param name → literal). `build_seed_world` turns them into `have:xxx` facts so a single-step search tool satisfies its preconditions without a prior producer step. `fill_literal_args` populates `step["args"]`; its defensive seed pass is gated by each step's candidate param schema (blind apply only when candidate lookup fails). `planner_node` also folds `seed_values` into `working_memory` at GOAP plan time for global context across iterations. Explicit merges in `context_check`/`builder`/`executor`/`_execute_step` plus a proxy-specific guard that skips the signature-based prune for `plugin_id` starting with `proxy_` (and the `_is_proxy_wrapper` marker on the callable) guarantee the literal reaches the wrapper's `**kwargs` → `provider.server.call_tool`. Resume/loop turns (which reset the plan and re-embed on `next_hint`) are covered by carrying `seed_values` + folded `working_memory`. Op-id mismatches for exotic proxy names (hyphens, case, numbers) are logged early.
9. **GOAP search→fetch chaining**: Collection ops (search/list/find) emit `have:id` effects. By-id consumer ops (fetch/get/read/…) with a literal `id` param derive a `have:id` precondition even when the schema omits `required`, so `_compile_steps` auto-binds `arg_bindings["id"] = "$steps[i].id"` without pollutating fetch args with unrelated seed literals (e.g. `query`).
10. **Plugin skills injection**: Plugins may declare `"skills": ["skills/xxx/SKILL.md"]` in manifest.json. Loaded (frontmatter stripped + capped + map) at resolver time, seeded to DB meta.skills on initial load, DB content preferred on subsequent loads/reinits (see loader + plugin_registry_store). Injected by `_format_plugin_skills(candidates)` **only** for candidate plugins, before "Available candidates:" in GOAP/linear prompts. Full add/mod/delete via UI (SkillsTab) + /api/plugins/{id}/skills. Gives goal agent workflow guidance.

---

## Dynamic State

The graph state uses `DynamicAPIState` to carry candidate routes, planner steps, and intermediate
results. It is **composed by TypedDict multiple inheritance** (repo-polish phase 5) from four
sub-states, purely for organization — flat at runtime, since a TypedDict is a plain `dict`:

```python
class TriageSubState(TypedDict):
    user_query: str
    force_execute: bool
    triage_mode: str | None
    # ... + retriage/decompose fields

class PlanExecutionSubState(TypedDict):
    candidates: list[RouteCandidate]
    plan: list[ExecutionStep]
    current_step_index: int
    step_results: list[dict]
    parallel_groups: list[list[int]]
    selected: RouteCandidate | None
    confidence: float
    gate_decision: Literal["execute", "confirm", "clarify"]
    clarification_question: str | None
    clarify_questions: list[dict] | None
    payload: dict | None
    response: dict | None
    retry_count: int
    replan_context: list[dict]
    last_failure: dict | None
    # ... + arg-resolution fields

class WorkflowSubState(TypedDict):
    session_id: str | None
    working_memory: dict
    last_summary: str | None
    goal: str | list[str] | None
    iterations: int
    max_iterations: int
    # ... + YAML-workflow + GOAP-goal-fact fields

class AuditSubState(TypedDict):
    messages: Annotated[list, add_messages]
    summary: str | None
    token_usage: dict
    model_audit: Annotated[list, operator.add]

class DynamicAPIState(TriageSubState, PlanExecutionSubState, WorkflowSubState, AuditSubState):
    """No fields of its own — see core_graph/states.py for the full field list per sub-state."""
```

`StateGraph(DynamicAPIState)` (`graph.py`), the Postgres checkpointer, and every node's flat
`state.get("...")` access work exactly as before the split — nothing about the graph's runtime
behavior changed. **Do not** nest a sub-state as a dict-valued field
(`state["triage"]["mode"]`) — that is not what this composition does and would break every flat
accessor plus the two reducers below.

To introspect the merged schema (e.g. to list every field, or check which carry a reducer), use
`typing.get_type_hints(DynamicAPIState, include_extras=True)` — **not** bare
`DynamicAPIState.__annotations__` (whether inherited fields show up there differs across Python
versions) and **not** a bare `get_type_hints()` call without `include_extras=True` (silently
strips `Annotated` metadata, turning `messages`/`model_audit` into last-write-wins fields that
silently drop concurrent writes under `parallel_groups`).

---

## Tool Calling & RouteRegistry

Tools are **not** hardcoded in the graph. Instead:
1. The planner decomposes the query using candidates from semantic search (`db_layer/embeddings`).
2. Tools are resolved at execution time via the central `RouteRegistry` (`core_graph/registry`).
3. **Fast-path tools**: Local `@mcp.tool` decorated functions are loaded directly as in-process callables (bypassing HTTP request overhead) via `static_tool_loader.py`.
4. **Dynamic tools**: Remote OpenAPI routes are converted to structured payloads and called over HTTP via `requests` under `safe_api_call`.

### Adding a Tool to the Agent
Any tool decorated with `@mcp.tool(tags={"core_mcp_plugin", ...})` inside `plugins/core_mcp_plugin/MCPTools/` is **automatically** collected on startup and contributed to the dynamic graph's registry via:
```python
from core_graph.registry.static_tool_loader import collect_from
routes = collect_from("core_mcp_plugin", ["plugins.core_mcp_plugin.MCPTools"])
route_registry.contribute(routes)
```
No manual import or schema mapping is required inside the graph code.

---

## Threading context between steps (`arg_bindings`)

A multi-step plan threads earlier outputs into later tool calls (e.g. a new
record's `id` → `record_id` for the next step). The planner emits
`arg_bindings` like `{"record_id": "$steps[0].id"}`; `_resolve_arg_bindings`
resolves them against `step_results` just before each step runs.

**Key gotcha — result envelopes.** `validator_node` wraps each step's output as
`{"status":"ok","data":{...}}`, so the real value lives at `$steps[0].data.id`,
not `$steps[0].id`. The planner guesses paths *blind* (before any step runs), so
`_resolve_arg_bindings` is deliberately tolerant — it tries the literal path,
then retries under common envelope keys (`data`/`result`/`response`/…), then
falls back to a recursive search for the final path segment as a key name. This
is why a wrong-path guess still finds the value instead of silently dropping it.

Resolution + literal `step["args"]` are applied in **three** places (keep them in
sync): the fast-path executor branch, the dynamic-HTTP executor branch (folds
resolved values into query params for GET/DELETE, body for writes), and the
parallel `_execute_step`. The **builder node** also receives the current step's
`intent`, threaded values, and a compact `step_results` summary so the LLM can
extract real prior values; threaded **path** params are substituted into the URL
in the builder (the executor only sees an already-built URL).

> When a chained tool call fails on a missing field that an earlier step
> produced, suspect (1) the binding path vs. the envelope shape, or (2) one of
> the three resolution sites missing the merge — not the planner.

## Gemini implicit caching — message ordering

The planner puts static, per-deployment content (`PLANNER_PROMPT` + environment
defaults) in the `SystemMessage` and the dynamic candidate list + user query
(query last) in the `HumanMessage`, so the stable prefix is as long as possible.
Gemini 2.5 models apply **implicit** caching to repeated prefixes automatically
(no `create_context_cache` needed) once a request crosses ~1K tokens — the small
system prompt alone won't trigger it, so don't expect large savings until the
stable prefix grows. See `llm-provider-enum` for the cached `get_chat_llm`.

---

## `run_graph` MCP Tool

Exposes the dynamic orchestrator as a headless MCP tool. Uses double-checked locking for thread-safe lazy graph compilation:

```python
_compiled_graph = None
_graph_lock = threading.Lock()

def _get_graph():
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph
    with _graph_lock:
        if _compiled_graph is not None:
            return _compiled_graph
        _compiled_graph = build_dynamic_graph(llm, route_registry=route_registry)
    return _compiled_graph
```

| Return Status | Scenario |
|---|---|
| `ok` | Plan completed successfully. Returns accumulated results. |
| `confirmation_needed` | Plan requires user validation (e.g. multi-step or long chain). |
| `clarification_needed` | Planner could not confidently match the request to any API routes. |
| `need_input` | Parameter payload builder is missing necessary fields. |
| `error` | Plan execution failed. |

### Elicitation Keepalive (in-process single-request human wait)
To keep a `run_graph` MCP request alive while blocked on `await ctx.elicit` (in confirm/clarify nodes), `_elicit` (core_graph/node/helpers.py) does:
- `_has_progress_channel`: only proceeds with elicit + keepalive if `request_context.meta.progressToken` present.
- Spawns `_elicit_keepalive` task: periodic `ctx.report_progress(..., {"type":"keepalive", ...})` using `ELICIT_KEEPALIVE_INTERVAL_S` (config default 15s).
- Uses try/finally + `contextlib.suppress(CancelledError)` to always clean the keepalive task.
- If no progress channel: immediately returns None (logger.debug) so node falls through to the `*_needed` response (two-call path). Signature/return contract unchanged.
- Configured via `graph.elicit_keepalive_interval_s` in server_config*.json.

Clients that support `report_progress` + elicitation will keep the request open for slow human responses.

### Structured Clarify Questions Contract
For status `clarification_needed` and `need_input`, the response envelope contains a `questions` array.
- Format: `questions: [{header, question, multiSelect, options: [{label, description, value}], value}]`
- Option-less questions (empty `options` list) represent free-text inputs (used for missing parameters).
- When a user submits clarifications, the client appends `\n\nClarification: <param>: <val>\n...` to the user query to trigger a replan.

---

## Permission Gate + Live Gateway (core_028)

- `permission_gate` node + `permission_gate_router` chokepoint before builder for context_check/step_resolver/retry entries.
- Policy loaded via `permission_store`; `operation_class` (promoted _WRITE_METHODS) decides read vs write.
- Defaults: reads allowed, writes require confirmation; per-tool overrides via `/permission` POST.
- `_set_tool_permission` request body validation uses a type-mapped Enum `PermissionField` and `PERMISSION_FIELD_TYPES` for dynamic validation and parameter forwarding to allow future schema extendability.
- `refresh_tools_summary()` rebuilds catalog string and calls `update_mcp_instructions`.
- `run_graph` docstring now points at `discover_tools`.

## Hybrid GOAP Planner & Autonomous Goal-Loop

- **Entry reset:** graph entry is `turn_init` → `triage`. `turn_init` clears transient
  per-turn fields (`plan`, `step_results`, `response`, `selected`, etc.) while
  preserving cross-turn memory (`working_memory`, `goal`, `last_summary`, `messages`).
- **Context-aware triage/chat** (`core_graph/node/triage.py`): both `triage_node` (chat-vs-task
  classifier) and `chat_node` (conversational reply) build a `format_context_block`
  (`core_graph/prompts/context_block.py`) from `messages`/`working_memory`/`last_summary` via the
  shared `_build_turn_context` helper and inject it as a `SystemMessage` before the user query.
  This is what lets a bare context-dependent follow-up ("mika?" after a prior turn about Tea Party
  members) route to `task` instead of a generic, context-free `chat` greeting — previously both
  nodes called the LLM with `HumanMessage(user_query)` alone, so the `TRIAGE_PROMPT`'s "if
  conversation context is present" clause had nothing to act on. Optional cross-session semantic
  memory enrichment (`core.memory.search_memory`) folds into the same block, gated off by default
  via `graph.triage.harness_memory_enabled` (`TRIAGE_CONFIG` in `utils/server_config.py`) — a
  lookup failure is swallowed (`try/except`, debug-logged) and never breaks routing. `triage_node`
  no longer stamps `response` itself; `chat_node` always makes the reply call.
  **Transcript is two-sided.** `chat_node` and `summary_node` append an untagged
  `AIMessage` of the user-facing text so the checkpointer's `messages` list (reducer
  `add_messages`) is not Human-only. Planner/decompose reasoning AIMessages carry
  `additional_kwargs={"internal": True}` and `history_from_messages` drops them unless
  `include_internal=True` — otherwise follow-ups like "search up!" resolve against
  the phrase, not the prior answer. `format_context_block` labels roles `user`/
  `assistant`/`system` (not the Python class name), keeps the last 12 entries
  (both sides), and truncates each to ~800 chars. `turn_init` emits `RemoveMessage`
  for all but the most recent 30 checkpointed messages (skip any without an `id`).
  Clarify replies (`confirm_clarify.py`) stay untagged — they *are* user-facing.
  Transcript alone is not enough for "search up!": `fill_literal_args` used to
  dump the raw `user_query` into `web_search.query`. Bare lookup imperatives are
  dropped as seeds (`is_bare_search_followup`) and replaced from the prior user
  topic via `resolve_search_query` (history, then `last_summary`).
  `apply_followup_search_topic` only rewrites missing/empty/imperative search
  seeds — a topical LLM seed is kept. Linear fallback prepends the same
  `format_context_block` so a GOAP miss still sees the two-sided transcript.
- **Hybrid planner (`planner_node`):** the LLM extracts goal + seed facts
  (`goap_goal_prompt`, with a conversation `context_block`); `core_graph/goap`
  derives actions from candidate route schemas (`derive.py`), runs A* forward search
  (`planner.py`) and **derives `arg_bindings` from action effects→preconditions**.
  `integrate.py` maps the GOAP plan onto `DynamicAPIState` (`plan`, `selected`,
  `instruction_set`). `build_seed_world` also injects `have:{k}` facts from `seed_values`
  so literal required params (e.g. `query`) satisfy GOAP preconditions before A* search.
  `fill_literal_args` post-fills `step["args"]` from seed_values then working_memory
  (defensive seed pass schema-gated); `seed_values` merged into `working_memory` at plan time;
  deterministic free-text fallback (when `user_query` is threaded) fills a single remaining
  leaf free-text/search param (from `_SEARCH_PARAM_NAMES`: query/q/search/search_query/keyword/term/text/prompt/name)
  only for an unambiguous leaf (no arg_bindings, only one unfilled required free-text param).
  `builder_node` missing-param validation uses `_get_parameters_schema()` (JSON-schema + flat proxy).
  Candidate hints are enriched: `_format_candidates` calls `_param_hints` to emit
  `name* (type): short-desc` (required marker, type, truncated desc) for both JSON-schema
  (`properties`+`required`) and flat proxy tool `{name:{required,type,description}}` shapes.
  In addition `_format_plugin_skills` (when relevant) prepends a "Plugin Capabilities / Skills" block carrying the manifest-declared workflow docs for that plugin.
  After `assign_step_models`, the GOAP path calls `inject_wait_steps(frag["plan"], candidates)`
  (`core_graph/goap/wait_inject.py`) to splice async **wait/poll** steps into the plan, and the
  confidence gate's `step_count` is recomputed from the lengthened plan.
  On any GOAP miss/exception it **falls back to the LLM linear planner** (unchanged).

  **Async wait/poll node** (`core_graph/node/wait.py`): a plan step flagged `kind:"wait"` (op = a
  status/poll op) is routed `builder → wait_node → validator` by `builder_router` (replaces the old
  unconditional `builder→executor` edge; a builder-emitted response passes through to `executor`).
  `wait_node` re-executes the builder-prepared poll payload via `_execute_single` up to `max_polls`,
  sleeping `interval_s`; `_find_record(resp, match)` locates the target record across list / `data` /
  `results` shapes by the `_resolve_arg_bindings`-resolved `match`; returns the resource when
  `record[until.field]==until.equals` (success → `next_step`), an error on a `fail_on` status, or an
  error on timeout. Wait steps are declared per-plugin via manifest `poll_specs` (loaded into
  `skill_registry`); the injector inserts them between an async create op and its consumer
  (`$create.*` rewritten to `$steps[create_idx].*`, `projectId` inherited, later `$steps[i]` refs
  shifted). Multi-select clarify (`build_clarify_questions` candidate fallback `multiSelect:True`,
  top-5) lets the user approve the whole chain at once; `format_clarification_hint` folds the picked
  ops into an ordered-chain directive for the replan query.
  `builder_node` skips its LLM YAML compile when `plan` is already populated
  (`if not yaml_workflow and not plan`). For pre-populated plans (like GOAP),
  `builder_node` serializes the plan to Conductor YAML via `serialize_plan_to_yaml`
  and saves it via `save_workflow_plan` exactly once per run.
- **Goal-loop:** `summary_node → goap_goal` (the `goap_goal` node replaces the old
  LLM-only `goal_check`). It folds step results into `working_memory`, refreshes
  `last_summary`, and accumulates `executed_op_ids`. It owns the goal as a structured
  GOAP entity: the planner persists `goal_facts` (`["did:X","have:Y"]`) once per goal,
  and each iteration `goap_goal` derives the world state (`world_from_memory`) and
  **deterministically** evaluates satisfaction (`evaluate_goal_facts` → achieved/remaining;
  `did:` matched against executed ops incl. proxy-prefix strip, `have:` against folded
  memory). **All sub-goals satisfied → done with NO LLM call.** Otherwise it falls back to
  the LLM verifier (`goal_check_prompt`); on continue it sets `user_query` to a focused
  `build_goal_directive` over `remaining_goal_facts` (which the planner injects into goal
  re-extraction to plan only the gap). With no `goal_facts` (linear-fallback planner) it is
  pure-LLM, identical to the old `goal_check`. Router `goap_goal_router` (= `goal_check_router`,
  keys on `goal_loop_decision`) `{"continue": planning_entry(), "done": END}` — `"decompose"`
  when decompose-first is on (default), `"embedder"` legacy. Terminates on goal
  satisfied / LLM final answer / `iterations >= max_iterations` (`DEFAULT_MAX_ITERATIONS = 20`)
  / no-progress guard. Pure helpers in `core_graph/goap/goal_loop.py`. When terminating with
  `done`, a terminal, non-None `response` is guaranteed (preserves existing response or builds
  one from `last_summary` / `incomplete` fallback). `make_goal_check_node` is kept exported
  (unwired) for backward compatibility.
- **Fan-out / count-aware multi-fetch:** `executor_node` delegates any step with
  `for_each`/`for_each_values` to the batch executor `_execute_step` (the sequential
  executor handles one item only); `context_resolution_router` routes such steps straight
  to `builder` (their id param binds per item via `$item`). `goap_steps_to_state` +
  `_inject_defensive_fanout` wire fan-out deterministically: search→fetch chains →
  `for_each:$steps[i].results` + `$item.id`; LLM `fan_out` descriptors match the proxy-prefixed
  step (`_strip_op_prefix`) and `_normalize_item_bindings` rewrites lingering `$steps[i].<field>`
  consumer bindings to `$item.<field>`. `detect_fanout_count` parses N from the query
  ("pick 5", "five", "both"; "all"/"each" → unbounded) into `fanout_target`, capped per step
  via `for_each_limit`. On a shortfall the `goap_goal` node replans: `collect_fetched_ids`
  (dash-insensitive substring match — fetched pages echo their id only inside a dash-less URL)
  counts fetched target ids and, while `< N` and under `MAX_REPLANS`, deterministically
  `continue`s with a `pending_fetch_ids` queue re-fetching only the missing ids.
- **Literal List Fan-out & Collapse:**
  - `inject_literal_list_fanout` detects literal lists with `len > 1` in `seed_values`. If a step accepts that parameter and does not already have a `for_each` or `for_each_values` set, it converts the list to `for_each_values` and rewrites the parameter's arg binding to `$item` (popping it from `args`).
  - `collapse_homogeneous_fanout` finds groups of steps with the same `operation_id` that differ only in a single parameter `p` in `args`. It collapses them into the first step of the group by setting `for_each_values = [each step's args[p]]`, sets the binding to `$item`, and deletes the duplicate steps. Finally, it re-indexes all `$steps[i]` references and `depends_on` lists to point to the correct surviving step index.

- **Invariants:** `response=None` on every (re)plan; plans > `LONG_CHAIN_THRESHOLD` (3)
  force `confirm`; 400/422 retry-once; triage chat/task split; `arg_bindings` resolved
  by `_resolve_arg_bindings` consistently across fast-path / dynamic-HTTP / parallel
  `_execute_step`.

---

## Postgres Checkpoint & Session Threading

To enable multi-turn memory across playground turns, a Postgres checkpointer (`langgraph-checkpoint-postgres`) is compiled into the graph.

### 1. Checkpointer Setup & Shutdown
The checkpointer is lazily initialized via `_get_checkpointer()`.
- Extracts `DATABASE_URL` and strips SQLAlchemy driver prefixes `postgresql+psycopg://` and `postgresql+asyncpg://` to conform to `psycopg/psycopg_pool`.
- Initializes an `AsyncConnectionPool` and `AsyncPostgresSaver`.
- Sets up database schema asynchronously: `_pg_saver.setup()`.
- Gracefully falls back to `None` if initialization fails, allowing the graph to execute ephemerally.
- **Teardown**: During server shutdown/teardown (`run_teardown(ctx)` in `core/bootstrap.py`), `shutdown_checkpointer()` is called to close the pool and clean up references so that subsequent initializations compile a fresh graph.

### 2. Session / Threading configuration
LangGraph requires a unique `thread_id` to persist and load thread states:
- Config helper: `_thread_config(session_id: str | None) -> dict` returns a config with a `thread_id` and a `"recursion_limit": 200`. If `session_id` is missing, it generates a unique ephemeral ID: `f"ephemeral-{uuid.uuid4()}"`.
- `session_id` is propagated from the playground API requests (`playground_chat` / `playground_stream_goap`) through `run_graph_impl`, `_run_graph_with_progress`, and `stream_graph_impl` to pass `config` parameters containing `thread_id` and recursion limit to `graph.ainvoke` and `graph.astream_events`. Concurrent executions under the same `session_id` are serialized using a per-session lock dictionary (`_session_locks`) to prevent checkpointer deadlocks or Postgres unique key constraint violation errors.
- `stream_graph_impl`'s `.values`/`.messages` consumer coroutines push a terminal `{"type": "error", "message": ...}` event onto the queue on exception (not just log), so `_run_graph_with_progress` surfaces a real `{"status": "error"}` to the MCP client instead of a stale mid-loop snapshot.
- `stream_graph_impl` also queries and yields the authoritative final state from `graph.aget_state` as the last `values` event before yielding the `end` event, allowing `_run_graph_with_progress` to capture the complete terminal envelope instead of a stale planner-stage snapshot. Additionally, `_finalize_graph_response` fallback is hardened to include any `summary` or `step_results` present in the end state.

---

## Structured Summary & Execution Persistence (core_025)

1. **Structured summary**: `SUMMARY_PROMPT` instructs the LLM to return JSON `{"summary": "...", "content": "...", "carry": {param: value}}`. `summary_node` parses this structured JSON with `_parse_json_response`, falls back to plain text on error, merges `carry` into the state's `working_memory` (so it propagates cross-turn), and populates `response` with `summary`, `message`, `content`, and `carry`.
2. **Durably recording executions**: Every graph execution and goal-loop iteration is recorded server-side in the `workflow_executions` table (linked to `workflow_plans`).
   - `execution_id` is tracked in `DynamicAPIState`.
   - On completion of plan steps, `step_dispatcher_node` generates an execution row via `create_execution` (if not already present) with `status="executed"`.
   - On final execution wrap-up, `summary_node` updates the execution row via `finalize_execution` with the structured summary, content, carry, and `status="done"`.
   - If the goal loop continues (`decision == "continue"`), the `goap_goal` node clears `execution_id` (sets to `None`) so the next iteration starts a fresh row.
   - All database calls are protected by try/except blocks with warning logging to never crash the graph execution on persistence issues.
3. **Session ID Injection**: `session_id` is passed to both `stream_graph_impl` and `run_graph_impl` and stored in `DynamicAPIState.session_id` and `execution_id: None`. This links workflow plans and executions to a chat/MCP session row.

---

## Dropped
- Obsolete `langgraph_flows` folder and `TOOL_SCHEMAS` references.
- Manual graph import/list of tools.
- Static multi-turn conversation saver logic (graph is dynamic per-plan).




## Permission Gate + Gateway Summary (core_028)

- permission_gate node + router inserted before builder (all paths: context, resolver, retry).
- Policies in 	ool_permissions (allow_read/write/require_confirmation); sparse = defaults.
- Live 
efresh_tools_summary() after toggles keeps mcp.instructions with catalog.
- Hot-swap fix: tool enable/disable reinits plugin for route re-contribution.

## Related
- `.claude/skills/model-role-specs/` — which node calls which model-role ladder
  (`core_graph/model_roles/`), `model_audit` state reducer, `NODE_ROLES` frontend contract.
