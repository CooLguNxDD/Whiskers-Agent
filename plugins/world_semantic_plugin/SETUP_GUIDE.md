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
  0008_unity_world_vector_dims_resync.py  # same rewrite; 0005 does not re-run after a dim change
  0009_require_asset_schema.py # guard only: requires Alembic core_051 world_asset_embeddings
  0009_world_vector_spaces.py  # unbounded vector width (per-model spaces), no row rewrite
  0010_world_tenant_owner.py   # nullable worlds.tenant_id for semantic v2 ownership (no backfill)
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
├── routes.py                 # POST index (enqueue) / diff / job status + semantic v2 routes
├── asset_adapters.py         # S04 authenticated asset MCP callables & HTTP bridge
├── world_adapters.py         # S04 authenticated world MCP callables & HTTP bridge
├── asset_index.py            # craft-v3 ingestion and multimodal asset search
├── world_index.py            # multimodal world inspect/snapshot ingest & search
├── world_documents.py        # inspect/snapshot envelope validation & formatting
├── worker/index_worker.py    # WorkerRegistry consumer
├── stores/world_store.py     # async SQL ingest + queries
├── stores/job_store.py       # enqueue / claim / status
├── stores/asset_store.py     # durable asset embedding staging & storage
├── stores/world_hex_store.py # durable world hex document & vector storage
├── encoder/                  # step 2 grammar + budget
├── summarizer.py             # step 3 dirty summaries
├── agents/orchestrator.py    # step 5 stage table
├── MCPTools/
│   ├── context_tools.py      # query_context, describe_region, …
│   ├── action_tools.py       # plan_placement
│   ├── lease_tools.py        # claim/release/renew
│   ├── critic_tools.py       # critique_region
│   └── semantic_tools.py     # S04 search_assets, search_world, index_assets, index_world
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

**Dimension rule:** `EMBED_DIMENSIONS` overrides the embedding provider's default width for all embed tables. Migration `0005` retypes `unity_world_vectors.embedding` to that resolved width. If the local embed API returns a different width, the worker pads/truncates to the resolved width before upsert.

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

## 9. Semantic V2 Ingest & Search (S04 Wiring)

Asset-library and world-hex semantic search are exposed as four registered
operations. They delegate to the S02 asset service (`asset_index.py`,
`stores/asset_store.py`, [ASSET_API.md](ASSET_API.md)) and the S03 world service
(`world_index.py`, `stores/world_hex_store.py`, [WORLD_HEX_API.md](WORLD_HEX_API.md));
there is no second embedding/index pipeline.

### Registration

| Operation | Tag | Scope (either grants) | MCP signature | HTTP route |
|---|---|---|---|---|
| `search_assets` | `read` | `group:world_semantic_plugin:read` / `plugin:world_semantic_plugin` | `search_assets(world_id, query=None, image=None, kind=None, tags=None, k=10)` | `POST /api/world/none/{world_id}/assets/search` |
| `index_assets` | `write` | `group:world_semantic_plugin:write` / `plugin:world_semantic_plugin` | `index_assets(world_id, index)` | `POST /api/world/none/{world_id}/assets/index` |
| `search_world` | `read` | `group:world_semantic_plugin:read` / `plugin:world_semantic_plugin` | `search_world(world_id, query, k=10)` | `POST /api/world/none/{world_id}/world/search` |
| `index_world` | `write` | `group:world_semantic_plugin:write` / `plugin:world_semantic_plugin` | `index_world(world_id, payload)` | `POST /api/world/none/{world_id}/world/index` |

- Tools: `MCPTools/semantic_tools.py` (imported by `MCPTools/__init__.py`); capabilities in `manifest.json`.
- Routes: `asset_adapters.register_asset_routes()` + `world_adapters.register_world_routes()`, called from
  `routes.register_routes()` in the plugin's `on_load`. They are registered with `AuthPolicy.NONE`
  because each handler **self-authenticates** the bearer; they are never anonymous.
- These four paths are the only semantic routes. There is **no** `/api/world/none/{world_id}/index` or
  `/api/world/none/{world_id}/search` alias. The legacy spatial routes (`POST /api/world/{world_id}/index`,
  `POST …/diff`, `GET …/index/jobs[/{job_id}]`, `GET /api/world/{world_id}`) are unchanged, stay public
  for the local Unity editor bulk path, and never call the semantic services.

### Setup prerequisites

1. **Migrations.** Core Alembic `core_051` (owns `world_asset_embeddings`) must be applied before the
   plugin loads (`0009_require_asset_schema` fails plugin load otherwise). Plugin migrations
   `0009_world_vector_spaces` (per-model vector widths) and `0010_world_tenant_owner` (nullable
   `worlds.tenant_id`) are applied by the plugin migrator (`auto_migrate: true`).
   ```bash
   python scripts/migrate.py                         # core Alembic chain incl. core_051
   python terminal/script/setup.py plugin_migrate    # plugin-local 0001..0010
   ```
2. **World ownership.** `0010` adds `worlds.tenant_id` with **no backfill**: every pre-existing world is
   unowned and answers `404 world_not_found` on the semantic world routes until an operator assigns it.
   The world row (and its projection) must already exist — created by the legacy spatial index or the
   Unity editor sync — then claim it once for the tenant (only unowned rows can be claimed; this never
   reassigns a world):
   ```sql
   UPDATE worlds SET tenant_id = 7, updated_at = now() WHERE world_id = 'demo' AND tenant_id IS NULL;
   ```
   or in-process `await plugins.world_semantic_plugin.stores.assign_world_tenant("demo", 7)`.
   Asset libraries do **not** need a `worlds` row: an asset `world_id` is a private library namespace
   inside the caller's tenant (rows keyed by `(tenant_id, world_id, asset_id)`).
3. **Model selection (Gemma / LiteLLM).** Both services require a multimodal embedding selection
   (`gemma-multimodal` provider, configured model alias, explicit positive `dimensions`); text-only
   providers are rejected, never silently substituted. Serving/adapter contract:
   [`docs/gemma-multimodal-embedding.md`](../../docs/gemma-multimodal-embedding.md). Either
   - set the shared profile: `EMBED_PROFILE=gemma_multimodal`, `EMBED_PROVIDER=gemma-multimodal`,
     `EMBED_MODEL=<LiteLLM alias>`, `EMBED_DIMENSIONS=<verified width>`,
     `EMBED_BASE_URL=http://host.docker.internal:<port>/v1` (plus `CAT_ALLOW_LOCAL_PROXIES=1` for a
     local gateway), or
   - register a `gemma-multimodal` embedding pool entry and map plugin `world_semantic_plugin` tools
     `asset_semantic` (assets) and `unity_world_semantic` (world hexes) to it in tool config.

   Vectors are stored per model identity `provider:model:dimensions`; changing the model or width
   starts a separate vector space (re-ingest to populate it) and searches read only the active one.
4. **Trusted roots (server config only).** Plugin `SETTINGS` (manifest `settings` / runtime config):
   ```json
   {
     "asset_ingestion_roots": {"7": {"library": "WorldGen/AssetLibrary"}},
     "world_snapshot_roots":  {"7": {"demo": "WorldGen/Snapshots"}}
   }
   ```
   Keys are the verified tenant id (string), then library/world id. Asset `reference.image` /
   `reference.audio` and a world `snapshot.data.path` are **relative** paths resolved under that root;
   absolute paths, `..`, symlink escapes and URLs are rejected and nothing is downloaded. Without an
   asset root `index_assets` fails `400`; without a snapshot root a world envelope must carry inline
   `snapshot.data.base64` (or omit the snapshot for a text-only hex).
5. **Queue readiness.** Ingest is asynchronous: both writes stage desired state and a durable
   `embedding_jobs` row in one transaction. The core `embedding_worker` (WorkerRegistry) claims pending
   jobs and dispatches `upsert_world_asset_embedding` (consumer registered on plugin load) and
   `upsert_world_hex_multimodal` to the plugin; completion is generation/revision guarded. Rows become
   searchable only once indexed/embedded. Failed jobs are retried by re-ingesting.

### Authentication, tenant and error policy

1. Bearer → `get_auth_service().principal_from_bearer` (MCP: the call's access token). No
   session-cookie, stdio or default-tenant bypass.
2. `Principal.tenant_id` must be an explicit integer > 0; requests never supply tenant identity.
3. Scope via `core.scope_management.evaluate_access` (`read` for search, `write` for index).
4. World operations only: `SELECT … FROM worlds WHERE world_id = :w AND tenant_id = :principal_tenant`.
   A world owned by another tenant, an unowned world and an absent world are the **same**
   `404 world_not_found` (MCP: `LookupError("world not found")`). The projection comes from that owned
   row; nothing runs after a denial (no projection, embedding, search, enqueue or write).
5. Every read/write stays in one tenant/world namespace: assets by `(tenant_id, world_id)` columns,
   world hexes by namespace `world-hex-v1:[tenant_id,"world_id"]` plus `meta.tenant_id`.
6. Bodies containing `tenant_id` or `trusted_root`, a `world_id` that differs from the path, a
   projection patch, or a reference path outside the trusted root are rejected with `400`.

| Condition | HTTP | MCP |
|---|---|---|
| missing / invalid bearer | `401 {"status":"error","error":"authentication_required"}` | `PermissionError("authentication required")` |
| no explicit tenant, or scope denied | `403 {"status":"error","error":"scope_or_tenant_denied"}` | `PermissionError` |
| world not owned by caller (foreign / unowned / absent) | `404 {"status":"error","error":"world_not_found"}` | `LookupError("world not found")` |
| invalid body / media / path / spoofed identity | `400`, `error` = `invalid_world_input` or `invalid_asset_input` | `ValueError` |
| body over limit | `413 {"status":"error","error":"payload_too_large"}` | — |
| provider / store failure | `503`, `error` = `world_service_failed` or `asset_service_failed` | exception |

Failures are never reported as a successful empty result.

### Examples

#### Asset ingest — `POST /api/world/none/library/assets/index`
Body is the versioned WorldGen asset index; `reference` paths are relative to
`asset_ingestion_roots["7"]["library"]`.
```json
{
  "version": 1,
  "assets": [{
    "id": "stone_bridge_01", "kind": "prop",
    "description": "Weathered stone arch bridge over clear water",
    "tags": ["stone", "bridge", "river"], "craftRoles": ["crossing"], "bounds": [10.0, 4.0, 5.0],
    "variants": [{"name": "default", "path": "Assets/bridge.glb",
                  "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],
    "defaultVariant": "default",
    "reference": {"image": "refs/stone_bridge_01.png", "audio": null}
  }]
}
```
`202 {"status":"enqueued","enqueued":1,"pending":0,"indexed":0,"warnings":[]}`. An unchanged
re-ingest after the worker ran returns
`200 {"status":"indexed","enqueued":0,"pending":0,"indexed":1,"warnings":[]}`.

#### Asset search — `POST /api/world/none/library/assets/search`
```json
{"query": "stone bridge over a river", "kind": "prop", "tags": ["stone"], "k": 5}
```
or by image: `{"image": {"mime_type": "image/png", "data": "<base64 PNG/JPEG bytes>"}, "k": 5}`
(exactly one of `query` / `image`). Response:
`200 {"status":"ok","assets":[{"asset_id":"stone_bridge_01","metadata":{…},"score":0.83}]}`.

#### World ingest — `POST /api/world/none/demo/world/index`
One hex per call: the Unity `world_inspect` result plus an optional snapshot, a monotonically
increasing `revision`, and the snapshot request center as `snapshot_center`. Coordinates are Unity
`[x, z]` metres in the world's established projection (here `demo`: anchor 12.0 / 25.0,
111320 m/deg, `base_res` 10). The inspect disc must fit in one H3 cell and `snapshot_center` must
match it. `hex_id` is optional (derived from `center`); if given it must agree.
```json
{
  "world_id": "demo",
  "revision": 1,
  "inspect": {"success": true, "message": "world_inspect", "data": {
    "ok": true, "center": [437.492, 763.275], "radius": 1.0,
    "histogram": {"grass": 10, "water": 5},
    "height": {"min": 1.0, "max": 4.0, "avg": 2.5}, "slope": {"0-5": 2},
    "waterPercent": 30, "waterLevel": 2.0,
    "propHistogram": {"willow_tree": 2},
    "waterBodies": [{"id": "serene_lake", "level": 2.0, "flow": [0.0, 0.0]}],
    "splines": [{"id": "trail", "kind": "footpath", "points": [[437.492, 763.275], [438.492, 764.275]]}]
  }},
  "snapshot_center": [437.492, 763.275],
  "snapshot": {"success": true, "data": {
    "ok": true, "path": "snapshots/demo/hex.png", "width": 2, "height": 2,
    "sizeX": 2, "sizeZ": 2, "view": "top"
  }}
}
```
`snapshot.data.base64` (PNG/JPEG bytes) may replace `path`. Response:
`202 {"status":"queued","world_id":"demo","hex_id":"8a6b0a380ae7fff","revision":1,"content_hash":"…","model_id":"gemma-multimodal:<alias>:<dims>","has_image":true}`.
Re-posting identical content returns `200` with `"unchanged"`; a lower revision returns `"stale"`;
reusing a revision for different content is `400`.

#### World search — `POST /api/world/none/demo/world/search` or MCP
```json
{"query": "lake near willow trees", "k": 5}
```
```python
await search_world(world_id="demo", query="lake near willow trees", k=5)
```
`200 {"status":"ok","world_id":"demo","results":[{"hex_id":"8a6b0a380ae7fff","center_pos":[437.49,763.28],"center_frame":"unity_xz_meters","summary":"…serene_lake…","score":0.81}]}`
(`center_pos` is the H3 cell centre in Unity XZ metres; MCP returns the `results` list).

### Offline mocked tests vs. Saturday human smoke test

- **Offline (automated):** `plugins/world_semantic_plugin/tests/test_semantic_v2_e2e.py` (plus the S02/S03
  suites) run in the server image with `--network none`. They drive the registered tools/routes, the real
  services, the real `embedding_worker` claim/dispatch/completion and the plugin consumers; only the
  embedding provider and SQL/session I/O are deterministic doubles. They prove wiring, isolation and
  error policy — **not** model quality or serving.
- **Saturday human smoke test (not yet done):** live Gemma multimodal serving behind LiteLLM, real
  Postgres/pgvector, Unity play-mode ingest, and the goal queries (`search_assets "bridge over a river"`,
  `search_world "lake near a village"`). Live GPU/model serving has **not** been verified.

---

## Related

| Resource | Path |
|---|---|
| Unity setup | `Packages/com.cclemon.worldcontext/SETUP_GUIDE.md` |
| Unity generator bridge | `Assets/_Scripts/WorldContextLink/SETUP_GUIDE.md` |
| Claude routing skill | `claude-plugins/world-context/skills/world-context-routing/SKILL.md` |
| Plugin system skill | `.claude/skills/plugin-system/PLUGIN_SYSTEM_SKILL.md` |
