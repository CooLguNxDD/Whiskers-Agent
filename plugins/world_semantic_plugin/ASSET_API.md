# Asset index API (S02 → S04 handoff)

S02 implements ingestion, durable consumption and thin authenticated adapters.
S04 owns final manifest/discovery/MCP registration and calling
`asset_adapters.register_asset_routes()` from the existing route-registration path.
Do not register a second embedding worker or route these jobs to unity vectors.

## Service signatures

In `plugins.world_semantic_plugin.asset_index`:

```python
await index_assets(body: dict, *, tenant_id: int, world_id: str,
                   trusted_root: pathlib.Path) -> dict
await search_assets(query: str | None = None, *, image: dict | None = None,
                    tenant_id: int, world_id: str, kind: str | None = None,
                    tags: list[str] | None = None, k: int = 10) -> list[dict]
```

These are trusted in-process services. `tenant_id` must be the authenticated
principal's explicit positive integer, not a client parameter or the defaulted
`current_tenant_id`. A world name identifies a private asset-library namespace
**inside that tenant**, not authorization to the legacy globally keyed `worlds`
table. S02 does not read or alter world/hex records.

The craft-v3 body is `{"version":1,"assets":[...]}`. Required fields per asset:
`id`, `kind` (`prop|block|texture|audio|stamp`), nonempty `description`, string
arrays `tags` and `craftRoles`, nonempty `variants` (`name/path/sha256`), and a
`defaultVariant` naming a variant. Optional `bounds` is three finite nonnegative
metres. `reference` may be omitted/null, or contain nullable `image` / `audio`
paths. Unknown additive metadata fields are ignored. Invalid input rejects the
whole batch **before any database write**; there is no false partial success.
Identity strings match `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`. Labels and variant order
are canonicalized, so reordering an index does not enqueue new work.

Limits: 32 assets/batch, 1 MB metadata JSON, 64 tags/craft roles, 32 variants,
8192-character descriptions/queries, 10 MB/reference, 24 MB combined references
per batch, and integer `1 <= k <= 100`. Submit larger libraries as bounded batches;
ingestion never clears assets absent from a batch.

## Media, representation and hashing

Reference paths are POSIX-relative to an **explicit operator-owned trusted root**.
Absolute paths, URLs, Windows drives, backslashes, traversal, nonregular files,
and symlinks resolving outside that root are rejected. The root and its contents
must not be writable by untrusted callers during ingestion. Resolve/stat/read,
Pillow image decode and PCM WAV validation run off the event loop. Supplied PNG,
JPEG and WAV must contain real valid bytes of the declared format; unsupported
extensions and malformed supplied content fail, never become text ingestion.
A genuinely missing optional file permits text-only (or the other available
reference) ingestion with an explicit per-asset `warnings[].missing_media` list.
A subsequently available file changes the hash and enqueues an update.

Jobs store immutable base64 snapshots of validated bytes, **not file paths** to
be reopened later. They contain no endpoint or credential. The existing S01 API
`embeddings_core.embed_multimodal_with(sel, [EmbeddingInput(...)])` receives
actual `EmbeddingMedia` bytes. Text-only records use one descriptive document;
image-only/audio-only references use one joint text+media document. A both-reference
record uses **two** inputs, in image-then-audio order, each containing the same
canonical text and one media item. `unit-mean-v1` normalizes each vector, averages
with equal weights, then normalizes the mean. Zero, cancelling, nonfinite or
wrong-width vectors fail. No padding/truncation, captioning, transcription, fake
vectors or text-only provider fallback is performed. Text and image queries use
that exact configured S01 multimodal space and unit-vector policy.

SHA-256 includes authorized tenant/world/asset identity, canonical metadata,
actual reference bytes, missing-media state, `provider:model:dimensions`, and
aggregation version. A same-path media replacement therefore changes content
identity. Config is resolved through `resolve_tool_embedding` for
`plugins.world_semantic_plugin` / `asset_semantic`; this falls through to shared
pool/environment/profile selection. The provider must declare a multimodal
factory, including for text-only assets/queries. The text-only
`embeddinggemma:300m` profile is not a substitute. Serving prerequisites remain
in `docs/gemma-multimodal-embedding.md`.

## Queue, status and migration ownership

`stores.asset_store.stage_assets` inserts/upserts the current desired asset and
its existing `EmbeddingJob` **in one transaction**. Each asset identity has one
row in `world_asset_embeddings`, keyed by `(tenant_id, world_id, asset_id)`.
Changed content clears only that asset's previous searchable vector. Unchanged
indexed or actively queued content is a no-op. A failed job is explicitly retried
by re-ingesting the same index, reusing the queue row rather than duplicating it.

The queue identity is:

- `plugin_id = "world_semantic_plugin"`
- `operation_id = "upsert_world_asset_embedding"`
- `content_hash =` namespace/content/model SHA-256

`WorldSemanticPlugin.on_load` registers `consume_asset_job` with the existing
`embedding_worker.register_embedding_consumer(plugin_id, operation_id, consumer)`.
`on_unload` unregisters only that pair. The core worker dispatches registered
consumers sequentially (bounded retained media); route/unity consumers retain
existing paths. Consumers own atomic vector/status publication and conditional
failure updates. S02 does not start another loop, make provider requests in
routes, or store asset embeddings in route/unity tables.

The producer and consumer use an asset-identity transaction advisory lock.
A random generation token in desired state and job payload prevents stale work,
including an A→B→A requeue reusing a hash/job id, from overwriting newer content
or marking a newer queue incarnation done/failed. Completion locks/checks the
live queue row and updates the generation/hash/model-matching desired row and
job status in one transaction. Failed calls/writes leave no indexed vector.
Provider errors are sanitized in logs/durable errors. Re-ingestion also repairs
failed queue rows left by core when the plugin was unloaded.

Ingestion returns `{status, enqueued, pending, indexed, warnings}`. `status` is
`enqueued` while any requested record is newly or already queued, and `indexed`
only when all requested records already have completed vectors (or the batch is
empty). The initial enqueue is not reported as indexing completion. Search sees
only `indexed` rows, and SQL applies tenant/world/model/kind/all-tags filters
before top-k. Hits have `{asset_id, metadata, score}`; metadata includes kind,
tags, description, craftRoles, bounds, variants, defaultVariant and references.
Empty results are legitimate and deterministic, not fabricated.

**Approved Alembic exception:** `migrations/versions/core/core_051_world_asset_embeddings.py`
(revises `core_050`) exclusively creates/drops this table and constraints. The
plugin owns ORM/store code in `asset_models.py` / `stores/asset_store.py`.
`migrations/0009_require_asset_schema.py` is a lifecycle existence guard only,
with **no duplicate DDL**; plugin loading fails closed until core migration is
applied. S03's `0009_world_vector_spaces.py` shares the `0009` prefix and sorts
after this guard; both revision ids are distinct, and tooling/tests must select
the guard by its full revision id, not by discovery position. Existing legacy `568ee6fe3f3f` and core heads are preserved. An operator
can use the existing `scripts/migrate.py upgrade core_051` (or its normal `heads`
path) in deployment; no live migration was performed by S02.

## MCP callables and HTTP registration for S04

In `plugins.world_semantic_plugin.asset_adapters`:

```python
await search_assets(world_id: str, query: str | None = None,
                    image: dict | None = None, kind: str | None = None,
                    tags: list[str] | None = None, k: int = 10) -> list[dict]
await index_assets(world_id: str, index: dict) -> dict
```

Bind these callables as MCP tools tagged `read` / `write`, respectively. They
self-check actual MCP bearer identity through `get_auth_service()` and
`evaluate_access`; absent authentication or an absent explicit tenant fails
closed, including stdio. No caller-selected tenant parameter is exposed.
Manifest capabilities/skills and `MCPTools/__init__.py` changes remain S04-owned.
The existing manifest already declares the read/write group scope vocabulary.

HTTP registration seam: `register_asset_routes()`, not invoked until S04 wires
it. POST routes:

- `/api/world/none/{world_id}/assets/search`: body `{query|image, kind?, tags?, k?}`;
  200 `{status:"ok",assets:[...]}`.
- `/api/world/none/{world_id}/assets/index`: craft-v3 index body;
  202 for queued work, 200 for already indexed/no-op.

Both are `AuthPolicy.NONE` **because handlers authenticate their own bearer**,
not because access is public. Require `Authorization: Bearer <validated API key
or OAuth token>`. No session-cookie-only or default-tenant bypass. Scope decision
uses `evaluate_access` for the plugin/read or plugin/write scope (normal
admin/all semantics apply). Missing/invalid bearer → 401, denied scope/tenant →
403, invalid inputs → 400, bounded body overflow → 413, service/config errors →
503. `tenant_id` and `trusted_root` in HTTP bodies are rejected.

Image representation is only inline:
`{"mime_type":"image/png"|"image/jpeg","data":"<strict base64 of actual file bytes>"}`.
Exactly one of nonempty `query` and `image` is required; ambiguous combinations,
paths, remote URLs and audio queries are rejected. Tags use AND containment.

Configure trusted roots **server-side**, e.g. manifest/runtime `SETTINGS`:
`asset_ingestion_roots = {"<verified tenant integer>": {"<world>": "<trusted repo-relative root>"}}`.
No deployment root is shipped or guessed; adapters fail until one is configured.
S04 should preserve this authorized mapping rather than accepting a root from
Unity/request payloads.

## Operational limitation

Provider/validation/persistence failures are retryable by re-ingestion; no
automatic retry storm is introduced. Re-ingestion also recovers asset-specific
`processing` claims older than one hour (process termination/persistent DB outage),
reusing the job row with a new generation. Recent processing claims remain no-ops.
This conservative lease exceeds S01's maximum individual request timeout; batches
can wait longer behind other claims, but generation guards ensure a recovered
incarnation cannot be clobbered by delayed work. Generic route/unity job lease
behaviour is unchanged. All verification uses synthetic files and mocked database/model transport with
network disabled, not a claim of live provider/DB/Unity integration.

---

# WorldGen v3 asset library (S11)

`index_assets` / `search_assets` (same tool names) also serve WorldGen v3 pack folders.
The pack format is owned by WorldGenEngineV3; this plugin only **consumes** it. Assumptions
made here: a pack is a folder `<root>/<pack_id>/` whose records carry a pack-computed
`content_hash`, frozen tags, a triangle count and one preview image per view.
The v1 craft-v3 contract above is unchanged and keeps working.

## Pack roots and distribution

Server-side config (never request data), next to `asset_ingestion_roots`:

```json
"asset_pack_roots": {"<tenant int>": {"<world>": {"packs": "<dir>", "packs-local": "<dir>"}}}
```

| root name | default `distribution` | meaning |
|---|---|---|
| `packs` | `shared` | shareable packs |
| `packs-local` | `local` | local-only packs; **never mirrored** |

Only those two names are accepted; each must be an existing directory. Views resolve to
`<root>/<pack_id>/<view path>`; absolute paths, `..`, backslashes, drives and any symlink that
resolves outside the root/pack are rejected (per record). A `packs-local` record can never be
`shared` (rejected); a `packs` record may declare the stricter `local`. A tenant/world is
**local-capable** only when it has a `packs-local` root configured.

## `index_assets` (version 3)

Request (MCP `index` argument, or the body of `POST /api/world/none/{world_id}/assets/index`):

```json
{"version": 3, "root": "packs", "assets": [{
  "asset_id": "forest-pack/props/oak.glb",
  "pack_id": "forest-pack",
  "content_hash": "3f9a5c1e0b7d4a62",
  "tags": {"role": "tree", "biome": "forest", "licence": "CC0", "free": ["oak", "tall"]},
  "distribution": "shared",
  "tri_count": 1800,
  "views": ["previews/oak_front.png", "previews/oak_side.png"],
  "caption": "A tall oak tree with a wide canopy",
  "kind": "prop"}]}
```

- `root` (required): `packs` or `packs-local`. Limits: 32 records per batch, 8 views per
  record, 10 MB per view, 24 MB of views per batch, `asset_id` at most 128 characters.
- `asset_id` = `<pack_id>/<rel_path>` (must start with `pack_id/`); `content_hash` is 8-128
  characters of `[A-Za-z0-9:_.-]`; `tags.role|biome|licence` are required strings, `tags.free`
  an optional list; `tri_count` a nonnegative integer; `kind` defaults to `prop`
  (`prop|block|texture|stamp`); `distribution` is optional (the root default applies);
  `views` are PNG/JPEG paths (at least one view or a `caption`); `caption` is optional.
- **content_hash semantics**: identity is `content_hash` + tenant/world + `asset_id` +
  `provider:model:dims`. The same hash for the same asset under the same vector identity is a
  **no-op** (no re-embed, no new job). A changed hash (or a changed model identity) re-embeds
  and replaces *only that asset's* vectors; the old ones are deleted when the change is staged,
  so the asset is not searchable until the new vectors are published.
- **Malformed records are isolated**: the batch continues and each bad record yields an
  `errors[]` entry. A malformed *envelope* (wrong version, unknown or unconfigured root,
  `assets` not a list, more than 32 records) rejects the whole call as `invalid_asset_input`
  (HTTP 400).

Response (`202` while work is queued, `200` when everything is already indexed):

```json
{"status": "enqueued", "enqueued": 1, "pending": 0, "indexed": 0, "warnings": [],
 "errors": [{"index": 1, "asset_id": "forest-pack/props/bad.glb",
             "error": "tri_count must be a nonnegative integer"}]}
```

Embedding runs in the existing asset worker consumer (same queue identity as v1). Per asset it
embeds **one image vector per view** (images carry no text, so no prefix) and **one caption
vector** (the caption uses the EmbeddingGemma *document* prefix), in Semaphore-bounded chunks
through the shared S01-S03 `embed_multimodal_with` client. Every vector is L2-normalised and
stored in `world_asset_vectors`, keyed `(tenant_id, world_id, asset_id, vector_kind, view_idx)`
with its `provider:model:dims` identity; vectors of another identity are never mixed into
scoring (S03 owns re-indexing old vectors). A failed embedding leaves no vectors and is retried
by re-ingesting.

## `search_assets` scoring and filters

```json
{"query": "tall oak", "k": 5, "role": "tree", "biome": "forest", "licence": "CC0",
 "distribution": "shared", "pack": "forest-pack", "max_tris": 5000}
```

(MCP: the same names as tool parameters; HTTP: `POST .../assets/search`.) A text `query` is
embedded as a **query** (`task: search result | query: ` prefix); an inline image query gets
no prefix.

**Scoring rule (max-sim):** an asset's score is the **maximum cosine similarity over its view
vectors, the caption vector counting as one more view**. Ties are broken by the **mean** cosine
over the same vectors, then results are ordered deterministically by
`(score desc, mean_score desc, asset_id asc)`. A v1 asset's single vector counts as one view.

```json
{"status": "ok", "assets": [{"asset_id": "forest-pack/props/oak.glb", "score": 0.91,
  "mean_score": 0.74, "metadata": {"kind": "prop", "pack_id": "forest-pack", "role": "tree",
  "biome": "forest", "licence": "CC0", "tri_count": 1800, "distribution": "shared",
  "tags": ["oak", "tall"], "views": ["previews/oak_front.png"], "caption": "..."}}]}
```

Filters are applied **in SQL inside `stores/asset_store.py`** (never post-filtered after a fixed
top-k): `role`, `biome`, `licence`, `pack` (exact match), `max_tris` (`tri_count <= n`; assets
without a `tri_count` are excluded), `distribution`, plus the existing `kind` / `tags`.
`tenant_id` (and `world_id`, vector identity, `status = indexed`) always filter.
v3 scoring runs when any v3 filter is supplied or the namespace has `asset_pack_roots`;
otherwise the legacy single-vector path runs unchanged.

**Distribution rules.** `distribution` defaults to `shared`. `local` and `any` are honoured
only for a local-capable tenant/world; otherwise the call fails with `scope_or_tenant_denied`
(HTTP 403 / `PermissionError`). A default (shared) listing therefore never contains local
assets. `distribution=local` assets are **never mirrored**: every outbound
mirror/sync/export/publish path must go through
`asset_adapters.mirror_assets(assets, sink, tenant_id=, world_id=)`, which skips (and logs
`asset mirror skipped local asset ...`) any asset whose metadata `distribution` is not exactly
`shared` (unknown or malformed fails closed) and never calls the sink for it.

## Errors

The HTTP/MCP error shape is unchanged: `{"status":"error","error":"<code>"}` with
`authentication_required` (401), `scope_or_tenant_denied` (403), `invalid_asset_input` (400),
`payload_too_large` (413), `asset_service_failed` (503). Per-record ingest errors live in
`errors[]` of a successful response, not in the error shape.

## Migration

Plugin migration `0010_asset_v3_library.py` (idempotent, guarded by the `core_051` table check)
adds `pack_id`, `distribution`, `tri_count`, `role`, `biome`, `licence` to
`world_asset_embeddings` and creates `world_asset_vectors`. Select it by full revision id
`0010_asset_v3_library` (a different `0010` revision, `0010_world_tenant_owner`, also exists).
