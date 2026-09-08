# world_semantic_plugin — Setup Guide

Whiskers Agent plugin for **H3 spatial store**, **Unity bulk index/diff ingest**, and **agent MCP tools** (`query_context`, leases, craft helpers).

Unity-side companion:  
`World-procedural-generation-agent/Packages/com.cclemon.worldcontext/SETUP_GUIDE.md`

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Whiskers Agent repo | This project |
| Postgres + **pgvector** | `docker-compose` or local |
| Python deps | `requirements.txt` including **`h3>=4.0.0`** |
| Plugin enabled | `config/plugin_config.json` (local; often gitignored) |
| Unity Editor (optional) | For full index + `world_bridge` craft loop |

---

## 1. Enable the plugin

`config/plugin_config.json` (local):

```json
{
  "tier": 100,
  "plugins": [
    "plugins.world_semantic_plugin"
  ]
}
```

Add `"plugins.world_semantic_plugin"` to your existing list if other plugins are already present.

Install deps if needed:

```bash
pip install "h3>=4.0.0"
# or: pip install -r requirements.txt
```

Restart the Whiskers Agent server so discovery + **schema auto-migrate** run.

### Migrations (plugin-local style)

```
plugins/world_semantic_plugin/migrations/
  0001_init.py                 # worlds, hexes, objects, edges, assets
  0002_lease_index.py          # lease expiry index
  0003_index_jobs.py           # durable world_index_jobs queue
  0004_unity_world_vectors.py  # dedicated Unity RAG vectors (not content_vectors)
  0005_unity_world_vector_dims.py  # retype embedding to global EMBED_DIMENSIONS
```

Each step exposes `upgrade(conn)`. With `manifest.json` → `schema.auto_migrate: true`, they apply on plugin load. Confirm rows in `plugin_schema_revisions` for `world_semantic_plugin`.

On load the plugin registers **`world_index_worker`** with `core_graph.worker.WorkerRegistry` and starts it. Core **`embedding_worker`** must also be running (default bootstrap) to drain Unity world RAG jobs.

---

## 2. Plugin layout

```
plugins/world_semantic_plugin/
├── manifest.json
├── plugin_config.py          # routes + worker start on load
├── hexmath.py                # H3 + meters↔lat/lng patch
├── routes.py                 # POST index (enqueue) / diff / job status
├── worker/index_worker.py    # WorkerRegistry consumer
├── stores/world_store.py     # async SQL ingest + queries
├── stores/job_store.py       # enqueue / claim / status
├── encoder/                  # step 2 grammar + budget
├── summarizer.py             # step 3 dirty summaries
├── agents/orchestrator.py    # step 5 stage table
├── MCPTools/
│   ├── context_tools.py      # query_context, describe_region, …
│   ├── action_tools.py       # plan_placement
│   ├── lease_tools.py        # claim/release/renew
│   └── critic_tools.py       # critique_region
├── migrations/
└── SETUP_GUIDE.md            ← this file
```

---

## 3. HTTP bulk API (Unity → Whiskers Agent)

**No LLM tokens** — Unity posts directly. **Index is queued**, not one-shot.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/world/{world_id}/index` | **Enqueue** index batch → `202` + `job_id` |
| `GET` | `/api/world/{world_id}/index/jobs` | List jobs + queue counts |
| `GET` | `/api/world/{world_id}/index/jobs/{job_id}` | Poll one job |
| `POST` | `/api/world/{world_id}/diff` | Batched create/update/delete (sync) |

### Index enqueue flow

1. Unity (or any client) POSTs a batch of objects (recommend ≤1000).
2. Server inserts `world_index_jobs` (`status=pending`), `NOTIFY world_index_jobs_new`, returns **202**:

```json
{
  "status": "queued",
  "job_id": 42,
  "world_id": "default",
  "objects_count": 1000,
  "replace": true,
  "queue_depth": 1
}
```

3. `world_index_worker` claims with `FOR UPDATE SKIP LOCKED`, runs `full_index`, then **enqueues Unity RAG embed jobs** into `embedding_jobs` (`operation_id=upsert_unity_world_vector`), marks index job `done` / `failed`.
4. Core **`embedding_worker`** embeds texts and upserts **`unity_world_vectors`** (object + hex docs). Isolated from `content_vectors` / `route_embeddings`.
5. Client polls `GET .../index/jobs/{job_id}` until `status` is `done` or `failed`. Job `result` includes `unity_embed_jobs_enqueued`.

Optional body fields for multi-batch runs: `batch_index`, `batch_total`, `client_run_id`.

Optional: `"embed": false` on the index body skips RAG fan-out (spatial tables only).

Escape hatch (blocking one-shot): `POST /api/world/{id}/index?sync=1` — not for large scenes.

### Unity world RAG (`unity_world_vectors`)

| Piece | Detail |
|---|---|
| Table | `unity_world_vectors` — `doc_kind` = `object` \| `hex` |
| Queue | `embedding_jobs` (same as routes), worker op `upsert_unity_world_vector` |
| Embed config | **Global** `EMBED_*` / `resolve_embedding()` — same `EMBED_DIMENSIONS` as routes |
| Optional tool key | `unity_world_semantic` (only if tool_config maps a pool entry) |
| Rank / search | `query_context` encoder + MCP `search_world_vectors` |
| Not used | `hexes.embedding` column vectors; not mixed into `content_vectors` |

**Dimension rule:** `EMBED_DIMENSIONS` is the global width for all embed tables (project default **1500**). Migration `0005` retypes `unity_world_vectors.embedding` to `VECTOR(EMBED_DIMENSIONS)`. If the local embed API returns a different width (e.g. 768), the worker pads/truncates to `EMBED_DIMENSIONS` before upsert.

```sql
-- after index + embed workers finish
SELECT world_id, doc_kind, doc_id, left(content_text, 80)
FROM unity_world_vectors
WHERE world_id = 'default';
```
| `GET` | `/api/world/{world_id}` | World projection metadata |

### Full index body

```json
{
  "replace": true,
  "world": {
    "name": "demo",
    "anchor_lat": 0.0,
    "anchor_lng": 0.0,
    "meters_per_degree": 111320.0,
    "base_res": 9
  },
  "objects": [
    {
      "guid": "any-stable-id-or-uuid",
      "name": "Player Camp",
      "pos": [0, 1, 0],
      "prefab_path": null,
      "components": ["Transform"],
      "tags": []
    }
  ]
}
```

Server assigns `hex_id` via H3 from `pos` (x,z) using the world projection.

### Diff body

```json
{
  "ops": [
    { "op": "upsert", "guid": "...", "name": "Tree", "pos": [10, 0, 5] },
    { "op": "delete", "guid": "..." }
  ]
}
```

Step 1 routes use `AuthPolicy.PUBLIC` for local editor use. Lock down for production.

---

## 4. MCP tools (agent control plane)

| Tool | Read/Write | Purpose |
|---|---|---|
| `query_context` | R | `raw=true` object list; `raw=false` encoder grammar (+ RAG rank) |
| `search_world_vectors` | R | Semantic search on `unity_world_vectors` (`object` / `hex`) |
| `describe_region` | R | Human-readable region / direction query |
| `get_hex` | R | Single hex row |
| `list_objects_in_hex` | R | Objects in one cell |
| `plan_placement` | R | Candidate hexes for a craft task |
| `claim_hexes` / `release_hexes` / `renew_lease` | W | Multi-agent leases |
| `critique_region` | R | Soft layout constraints |

### Milestone queries

```
describe_region(world_id="demo", anchor_name="player camp", direction="north", k=3)

query_context(
  world_id="demo",
  task="find flat defensible ground",
  anchor_name="player camp",
  raw=false,
  token_budget=800
)
```

### Craft loop (with Unity)

1. Unity: Full Index + Auto-Wire generators (Play Mode for carve).
2. Agent: `plan_placement` → optional `claim_hexes`.
3. Agent: unity-mcp **`world_bridge`** spawn / carve.
4. Agent: re-`query_context` / `describe_region` to verify.
5. `release_hexes` + optional `critique_region`.

Claude plugin (optional): `claude-plugins/world-context`  
commands: `/world-index`, `/world-query`, `/world-craft`, `/world-critic`.

Install marketplace entry:

```bash
claude plugin marketplace add <path-to-whiskers-agent-repo>
claude plugin install world-context@whiskers-oct
```

---

## 5. Projection must match Unity

| Param | Unity `WorldContextConfig` | Whiskers Agent `worlds` row |
|---|---|---|
| world id | `worldId` | path `{world_id}` / column |
| anchor | `anchorLat`, `anchorLng` | `anchor_lat`, `anchor_lng` |
| scale | `metersPerDegree` | `meters_per_degree` |
| res | `baseRes` | `base_res` |

Defaults: lat/lng `0,0`, `111320` m/°, res `9`.

---

## 6. Verify

1. Server starts without plugin load errors.  
2. Migration revisions present for `world_semantic_plugin`.  
3. `POST /api/world/demo/index` with a tiny payload → `status: ok`.  
4. MCP: `describe_region` / `query_context` returns the object.  
5. Unit tests (no DB):

```bash
pytest test/unit/test_world_semantic_hexmath.py \
       test/unit/test_world_semantic_routes_paths.py \
       test/unit/test_world_semantic_encoder.py -q
```

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| Plugin not listed | `plugin_config.json` entry + restart |
| Migration fail | Postgres up; `vector` extension; `EMBED_DIMENSIONS` if needed |
| `h3` import error | `pip install h3` |
| Index 500 | Check logs; ensure migrations applied; valid JSON body |
| Empty `describe_region` | Index first; name substring must match object `name` |
| Embedding rank no-op | Configure embed provider; summarizer falls back to deterministic text |
| Carve / spawn | Handled in **Unity**, not this plugin |

---

## 8. Architecture (hybrid transport)

```
┌─────────────────┐  HTTP bulk index/diff   ┌──────────────────────────┐
│  Unity Editor   │ ──────────────────────► │  world_semantic_plugin   │
│  + worldcontext │                         │  Postgres + H3 + tools   │
│  + unity-mcp    │ ◄── MCP world_bridge ── │                          │
└────────▲────────┘                         └────────────▲─────────────┘
         │ MCP exec / verify                              │ MCP query
         └────────────────── Agent (Claude) ──────────────┘
```

Bulk scene data never passes through the LLM context window.

---

## Related

| Resource | Path |
|---|---|
| Unity setup | `Packages/com.cclemon.worldcontext/SETUP_GUIDE.md` |
| Unity generator bridge | `Assets/_Scripts/WorldContextLink/SETUP_GUIDE.md` |
| Claude routing skill | `claude-plugins/world-context/skills/world-context-routing/SKILL.md` |
| Plugin system skill | `.claude/skills/plugin-system/PLUGIN_SYSTEM_SKILL.md` |
