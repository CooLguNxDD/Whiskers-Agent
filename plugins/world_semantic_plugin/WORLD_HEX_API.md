# S03 world-hex ingestion and search (S04 integration contract)

This service is additive. It does **not** register MCP tools, catalog operations,
HTTP routes or a Unity bridge, and it does not fetch/render snapshots. S04 owns
that assembly. Existing public scene-index routes must **not** call it without
adding authentication and world authorization.

> S04 wiring: the registered surface is `world_adapters.py` (MCP `search_world` /
> `index_world`, `POST /api/world/none/{world_id}/world/{search,index}`). It
> authorizes a world only when `worlds.tenant_id` equals the principal tenant
> (`stores.get_world_for_tenant`, migration `0010_world_tenant_owner`); foreign,
> unowned and absent worlds are the same not-found. See SETUP_GUIDE.md §9.

## Server-only authorized context

```python
from plugins.world_semantic_plugin.hexmath import projection_from_world_row
from plugins.world_semantic_plugin.world_documents import AuthorizedWorld
from plugins.world_semantic_plugin.world_index import index_world, search_world

# S04: authenticate principal, enforce scopes through ScopeManager, check world
# membership, then load its ESTABLISHED projection. Never accept tenant_id or
# AuthorizedWorld from a client body. Do not create/reset projection on ingestion.
context = AuthorizedWorld(
    tenant_id=principal.tenant_id,
    world_id=authorized_world_id,
    projection=projection_from_world_row(authorized_world_row),
)
queued = await index_world(payload, context=context, trusted_root=operator_root)
hits = await search_world("lake near a village", 10, context=context)
```

Signatures:
- `async index_world(payload: dict, *, context: AuthorizedWorld, trusted_root: Path | None = None) -> dict`
- `async search_world(query: str, k: int = 10, *, context: AuthorizedWorld) -> list[dict]`
- `normalize_document(context, payload, model_id, *, trusted_root=None) -> WorldDocument`

No arbitrary world/tenant selection or missing-context fallback exists. The
context is an internal capability, **not** proof of authorization by itself: its
constructor validates identity/projection, while S04 must enforce principal
membership and scope before creating it. Root paths are operator configuration,
never request parameters. They must be immutable to untrusted callers during
reads (including symlink targets). S04 must bound the HTTP body before parsing;
the service additionally caps image bytes (10 MB), semantic metadata (1 MB),
query length (10,000 characters), nested metadata depth/collections and k (50).
Each call indexes exactly one hex; split multi-hex batches into bounded calls.

## Actual Unity input envelopes

```json
{
  "world_id": "demo",
  "revision": 42,
  "inspect": {
    "success": true,
    "message": "world_inspect",
    "data": {
      "ok": true, "center": [100, 200], "radius": 1,
      "histogram": {"stone": 10, "grass": 3},
      "height": {"min": 1, "max": 5, "avg": 3},
      "slope": {"0-5": 4}, "waterPercent": 20, "waterLevel": 2,
      "propHistogram": {"oak_tree": 2},
      "waterBodies": [{"id": "lake", "level": 2, "flow": [1, 0]}],
      "splines": [{"id": "road", "kind": "road", "points": [[99, 199], [101, 201]]}]
    }
  },
  "snapshot_center": [100, 200],
  "snapshot": {
    "success": true,
    "data": {
      "ok": true, "base64": "<actual PNG base64>",
      "path": "<ignored Unity-local path when base64 exists>",
      "width": 64, "height": 64, "sizeX": 2, "sizeZ": 2, "view": "top"
    }
  }
}
```

This is a shape example, not executable media. Sample points/radii must actually
fit the authorized world's hex. `hex_id` may be supplied (validated H3 at the
world's base resolution and matching inspect center) or omitted (derived using
`hexmath.cell`). Optional `projection` must exactly match established parameters.
A supplied tenant must match context; it cannot authorize anything.

Inspect adapts `SuccessResponse.data` or the raw `WorldInspect.Sample` result.
Legacy histogram/height/slope/water/prop fields retain their meanings; optional
craft-v3 `waterBodies`, `splines`, `waterDepth`, `flow` are included only if supplied.
No water-body/spline topology is inferred from waterPercent, and no props are
invented for absent propHistogram. The read-only sibling currently lacks the
optional craft-v3 inspect extensions: this service accepts the additive fields
without asserting they are already emitted. Streaming/performance statistics,
file paths, messages and capture timestamps are not semantic content.

`WorldSnapshotRaster.Capture` supplies `base64/path/width/height/sizeX/sizeZ/view`
but does **not** echo center. `snapshot_center` therefore must be the saved
snapshot **request** center; an additive `snapshot.data.center` is also accepted.
The center must match inspect and the footprint must fit the same cell. S04 must
pair the corresponding request/results and monotonic revision, not attach an
arbitrary latest screenshot. PNG is the default MIME; `mime_type: image/jpeg`
is supported when the actual bytes match. Supplied dimensions are checked.

Base64 takes precedence over path: invalid supplied base64 fails, even if the
path exists. Without base64 a relative path is read only beneath trusted_root;
absolute paths, URLs, traversal and escaped symlinks fail. A Unity-local path
alone is **not remotely readable**. Omit `snapshot` (or use null) for explicit
text-only documents in the **same selected multimodal vector space**. Supplied
invalid media is never converted to text-only. Audio is not an S03 snapshot.

## Spatial association and centers

Unity XZ meters are mapped onto the existing small H3 geographic patch by
`hexmath.Projection`. The inspect **disc must be contained in the target cell**
(using projected boundary-edge distances); broad region aggregates are rejected,
not copied into every cell they touch. A disc is a local sample, **not evidence
about every point of the hex**; summaries state that distinction explicitly.
Snapshots are contextual imagery for this same local sample; the bounding disc
of sizeX/sizeZ must also fit the cell. The service does not resample or partition
aggregate histograms. Supply spatially associated per-hex results instead.

Search returns actual H3 centers via `cell_center_meters`, using the authorized
world's established projection. `center_pos` is **[x,z]**, frame
`unity_xz_meters`; no unknown Y is fabricated and raw lat/lng is never mislabeled.
Freeze projection for an indexed world. If its projection is intentionally
changed, rebuild documents in that new established frame before serving search.

```json
[{"hex_id": "<real H3 cell>", "center_pos": [123.4, 234.5],
  "center_frame": "unity_xz_meters", "summary": "World demo; hex ...",
  "score": 0.87}]
```

## Hash, revision and queue integration

Model selection is `resolve_unity_world_embedding()` (existing tool/global pool
configuration); S01 validates its multimodal capability. No model/width/endpoint
is hardcoded or downgraded. Model identity is provider:model:dimensions; queries
use S01's text-query API in that same selected vector space. Changing selection
creates a separate identity; queries never blend models. Canonical numeric
configured widths are normalized before SQL search.

`unity_world_vectors` is reused with **doc_kind=world_hex** and
`world_id='world-hex-v1:' + compact_json([tenant_id, world_id])`. This key plus hex
plus model protects tenant/world identity without adding a tenantless filter or
colliding with legacy raw-world object/hex/hex_layer documents. Migration 0009
removes the legacy global vector typmod without deleting/transforming any vector
or changing S02 migrations/modules. There is no global-width padding/truncation
in this path. Dense search filters namespace, kind, model and exact width before
ranking/limit; pending NULL vectors cannot become sparse matches. A future ANN
index must be per-model/width partial expression indexing, not a global-width
index. Legacy search/encoder code is unchanged.

Content hash includes namespace, hex, model, canonical summary (including
projection and snapshot footprint), MIME and **exact image bytes**. Dictionary
order, histogram order, water-body/spline/structure collection order and tag order
are canonicalized; spline point sequences/flow vectors retain order. Revision,
paths, capture filenames and runtime counters do not affect the hash.

`revision` is a positive 64-bit monotonically increasing source revision per
world/hex. S04 must preserve it across retries; the same revision with different
content is rejected. Under row lock, ingestion reserves the newest revision;
unchanged indexed content retains its vector and enqueues nothing. Changed
content clears only this row's vector, stores the new summary/hash, and enqueues
atomically in `embedding_jobs`. Queue identity includes content hash + revision
to handle ABA/repeated content safely. Retrying the same failed/current revision
resets only FAILED jobs, never PROCESSING or DONE claims.

The already registered `embedding_worker.process_batch` delegates
**upsert_world_hex_multimodal**, validating plugin_id **world_semantic_plugin**;
no additional worker is created. Plugin-owned `process_world_hex_jobs` processes
one document at a time, using actual validated bytes in
`EmbeddingInput(text=summary, media=EmbeddingMedia(...))` and S01
`embed_multimodal_with`. Worker completion updates only matching
namespace/kind/hex/model/hash/revision. Old in-flight work updates zero rows and
finishes as a stale no-op. Wrong width, model drift, image/provider or persistence
failure marks the job FAILED with a sanitized message; no vector is indexed.
An identical reingest can retry after failure. A model change requires reingest
in the new model space, not silently completing an old job with a different model.
No media, endpoint, credentials or provider errors are logged.

Index adapter status is `queued | unchanged | stale`; `queued` is **not indexed**.
Search returns ordered, unique hexes or `[]` for an empty index. k is a positive
integer clamped to 50. Validation failures raise ValueError (S04 may map to 400).

## Offline verification

`tests/test_world_hex_index.py` exercises real normalization, reservation/job SQL,
registered-worker delegation, exact joint bytes, failures/retries, stale CAS,
real shared search-engine query selection/filtering, tenant/world/model isolation,
projected centers, legacy mapping and preservation-only migration. All media is
synthetic and DB/provider boundaries are mocked; no Unity/model/network is used.
