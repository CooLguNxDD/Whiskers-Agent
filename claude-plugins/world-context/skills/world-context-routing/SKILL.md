---
name: world-context-routing
description: Route Unity world-semantic tasks between unity-mcp (editor exec) and Whiskers Agent world_semantic_plugin (spatial DB). Use for scene index, region queries, craft loops, carve, and multi-agent leases.
---

# World Context Routing

Three parties:

| Party | Role | Transport |
|-------|------|-----------|
| Unity Editor | Runs unity-mcp server; indexes scene via `com.cclemon.worldcontext` | Local editor |
| Claude CLI / agent | MCP client of **both** servers | MCP tools |
| Whiskers Agent | `world_semantic_plugin` (Postgres + H3 + pgvector) | MCP + HTTP |

## Critical rule — bulk data never through the agent

**Do not** dump full scene hierarchies into chat or re-index by calling unity-mcp object-by-object through the LLM.

| Data path | How |
|-----------|-----|
| Full index / diffs | Unity HTTP → `POST /api/world/{world_id}/index` and `/diff` (Editor window or ChangeWatcher) |
| Context queries | Agent → MCP: `query_context`, `describe_region`, `get_hex`, `list_objects_in_hex` |
| Scene execution | Agent → unity-mcp `world_bridge` (`spawn`, `move`, `destroy`, `set_property`, `carve_terrain`, `sample_height`, `scatter`) |

## World id & projection

- `world_id` is a string key (e.g. `default`, scene name). Must match Unity `WorldContextConfig.worldId`.
- H3 uses a meters→lat/lng patch. Server assigns `hex_id` from object `pos` using world projection params.
- Default res = 9; override via world row / config.
- **Hex → Unity position**: use `center_pos` `[x, z]` from `plan_placement` candidates or `get_hex` (world meters). Sample Y via `world_bridge sample_height` before spawn.

## Workflows

### Index a scene

1. Confirm Whiskers Agent is up and `world_semantic_plugin` is loaded.
2. Open Unity **Window → World Context → Indexer**, set config (`baseUrl` e.g. `http://127.0.0.1:10000`), click **Full Index** — or menu **Window → World Context → Full Index Now**.
3. Enable **Live Diff Watcher** (persists across domain reload via EditorPrefs).
4. Do **not** stream every GameObject through this chat.

Slash: `/world-index`

### Query spatial context

Use Whiskers Agent MCP tools (not unity-mcp for reads of the semantic store):

```
describe_region(world_id="default", anchor_name="player camp", direction="north", k=3)
query_context(world_id="default", anchor_name="player camp", direction="north", k=3, raw=true)
get_hex(world_id="default", hex_id="...")   # includes center_pos [x,z]
list_objects_in_hex(world_id="default", hex_id="...")
```

Milestone: “what’s north of the player camp?” → `describe_region` with `anchor_name` matching a scene object name (case-insensitive substring).

Slash: `/world-query <question>`

### Craft loop (validate-before-execute)

Slash: `/world-craft <task>`

Order is fixed:

1. `query_context` → situation
2. `plan_placement` → hex + **`center_pos`** + **`neighbor_hexes`**
3. **`claim_hexes` mandatory**
4. **`critique_region` pre-gate** (fail → re-plan, no spawn)
5. `world_bridge sample_height` + `spawn` (individual landmarks) or `scatter` (bulk patches, one call per patch, `y_mode=analytic` for ground snap without a raycast hit) / optional `carve_terrain`
6. Confirm diff flush; re-query until dirty cleared / summaries updated (scatter's indexed `parent_name` GameObject carries the patch summary — its `__`-prefixed children are index-ignored)
7. `release_hexes` + final critique

### Carve terrain

- **In scope** when: Play Mode + scene has bootstrapper (`BlockVoxelWorldBootstrapper` / `VoxelWorldBootstrapper`) + **Window → World Context → Auto-Wire Generators + Hex Gizmos** (registers `WorldContextGeneratorBridge`).
- Call: `world_bridge` action `carve_terrain` with `pos`, `radius`, `strength` (+add / -subtract), optional `engine=block|voxel|auto`.
- Prefer durable prop spawns in **Edit Mode**; after Play Mode carves, full re-index (replace) if scene/DB may have diverged.

## When to use unity-mcp vs world_semantic

| Need | Server | Tools |
|------|--------|-------|
| Hierarchy inspect / play mode / components (editor) | unity-mcp | `manage_scene`, `manage_gameobject`, … |
| “What is around X in the indexed world?” | Whiskers Agent | `query_context`, `describe_region`, `get_hex`, … |
| Bulk re-index | Unity HTTP only | World Context window / Full Index Now |
| Spawn/move/destroy/carve/sample height/scatter | unity-mcp | `world_bridge` |
| Placement plan + leases + critique | Whiskers Agent | `plan_placement`, `claim_hexes`, `release_hexes`, `critique_region` |

## Tool map

| Step | Tools / commands |
|------|------------------|
| Encoder | `query_context(raw=false, task=..., token_budget=...)` → grammar tokens |
| Embeddings | dirty hex summaries refreshed on encode; embedding rank when configured (deterministic fallback OK) |
| Craft | `plan_placement` (center_pos) + unity-mcp `world_bridge` (`spawn` landmarks, `scatter` bulk patches) + `/world-craft` |
| Multi-agent | `claim_hexes` / `release_hexes` / `renew_lease` + `critique_region` + `/world-critic` |
| Terrain carve | `world_bridge carve_terrain` (Play Mode + Auto-Wire) |

## Out of scope / deferred

- GOAP in-process wrap of the craft loop (CLI agent is the driver).
- RAG vector refresh on every diff (deterministic summaries suffice for milestone).

Keep the system modular: generators stay independent of the world-context package; bridge via Auto-Wire only.
