# Model Role Specs (`core_graph/model_roles/`)

Declarative replacement for hand-coded model-selection cascades. A pipeline role (`triage`,
`planner_goal`, `summary`, `specialist`, ...) is a `ModelRoleSpec`: an ordered `ladder` of `Rung`s,
a declared `Validation`, pre-emptive `entry_conditions`, reactive `escalate_on_*`, and a
`terminal_fallback`. `core_graph/model_roles/ladder.py::run_role_ladder` is the single executor every
node calls — adding a tier or retargeting a role is a JSON/DB edit, never new node Python.

Ships behind `MODEL_ROLES_ENABLED` (`utils/server_config.py`, default **off**). Every built-in ladder
in `defaults/core_roles.json` is `[{"selector": "core"}]`, so the layer is a no-op on routing until an
operator declares real rungs — but `model_audit`/token accounting are always recorded.

## The effort vocabulary (plugin-author-facing contract)

Plugin manifests never name a model directly. They declare **effort**:

```jsonc
"settings": {
  "specialist_agent": {
    "effort": "high",                      // domain-wide default
    "effort_overrides": { "discover": "low" }  // optional, per goal_class
  }
}
```

`low | medium | high | max` map through the core-owned `effort_map`
(`{"low":"fast","medium":"balanced","high":"strongest","max":"strongest"}` by default) to a selector
alias, then to a numeric strength hint resolved against the active `llm_pool` (`core_graph/model_roles/
resolver.py::compile_selector`). The map is edited fleet-wide via the admin console (Config → Model
Roles) or `db_layer/model_role_store.py::set_effort_map` — retuning "high" retunes every plugin that
declared `"effort": "high"`, no plugin edit needed.

Rung selectors, in full: an alias (`core`/`fast`/`balanced`/`strong`/`strongest`/`weakest`), an
`effort:<level>` token, an explicit pool-entry name, or a raw numeric strength string.

## Precedence chains

**Role registry** (`registry.py::ModelRoleRegistry.get`): **DB override > core default > plugin**. A
plugin registering a core-owned `role_id` is rejected with a warning, never merged.

**Specialist-stack model selection** (`selection.py::select_agent_model`), first non-null wins:
`StageSpec.model` > `StageSpec.effort` > `FlowSpec.effort` > manifest
`effort_overrides[goal_class]` > manifest `effort` > `SubgraphSpec.model_role`/`effort` > core role
`"specialist"` > `get_graph_core_llm()`. Implemented once — no node or runner re-implements it.

## Declaring a role

New file mirrors `core_graph/subgraphs/specialist/flow_spec.py`'s pattern exactly: frozen dataclasses,
strict `_*_KEYS` allowlists, fail-closed (`ModelRoleSpecError`, never silently repaired). Plugins
contribute via manifest `settings.model_roles: ["model_roles/my_roles.json"]` (path relative to plugin
dir, `..`/absolute rejected — same guard as `flow_specs`) or `PluginContext.contribute_model_role`.

## The node->role frontend contract

`core_graph/model_roles/node_bindings.py::NODE_ROLES` is the **single source of truth** for which
graph node resolves which role_id(s). `test/unit/test_node_role_bindings.py` regex-scans real
`run_role_ladder(...)`/`llm_for_role(...)` call sites and fails CI if a cutover forgets to update this
map. `scripts/gen_graph_topology.py` compiles it into `frontend/.../components/goap/modelRoles.gen.ts`
(role_id union, effort/alias vocabulary, `NODE_ROLES` typed against the sibling `graphTopology.gen.ts`'s
`NodeId` — a node-id typo there is a TypeScript compile error, not silent drift). Never hand-edit either
generated file.

## Operator surface

- **DB overrides**: `server_settings['model_role_specs']` = `{"roles": {...}, "effort_map": {...}}`
  (`db_layer/model_role_store.py`). Strict validation on write (`ModelRoleSpecError` → 400 in
  `api/config_routes.py`'s `GET/POST config/model-roles`, `DELETE config/model-roles/{role_id}`),
  lenient skip-and-log on read (a stored spec that no longer parses is dropped, never crashes the
  graph). `core_graph/model_roles/db_overlay.py::apply_db_overrides()` loads the store into the
  registry/resolver — called once at boot and again after every config-route write.
- **Admin console**: Config → Model Roles (`ModelRoleSection.tsx`) — per-role ladder editor with a
  live resolved-model preview per rung, plus the effort_map editor.
- **Playground**: `model_audit` (state reducer, `Annotated[list, operator.add]`) streams through the
  existing state snapshot with zero backend change; `components/goap/modelAudit.ts::auditByNode`
  groups it per node for the GraphCanvas chip + inspector Model block.
- **TUI**: `terminal/tui/setup_tui.py::_screen_model_roles` (Live Settings → 4) — ladder-as-CSV,
  effort_map, reset-to-default. Full Validation/entry_conditions editing stays admin-UI-only.

## Related

- `.claude/skills/langgraph-chain/` — graph nodes/state this layer plugs into.
- `.claude/skills/plugin-system/` — manifest contribution path (`settings.model_roles`,
  `specialist_agent.effort*`).
- `.claude/skills/llm-provider-enum/` — the provider registry `resolve_role_llm` bottoms out into.
