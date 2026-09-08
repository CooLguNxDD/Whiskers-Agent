---
description: Skill-driven craft loop — query context, plan placement, claim, validate, exec via world_bridge, verify
argument-hint: "<task> [world_id=default] [agent_id=craft-1]"
---

You are running a **world-craft** loop. Args: $ARGUMENTS

## Loop (do not dump full scenes into chat)

1. Parse `task`, optional `world_id` (default `default`), `agent_id` (default `craft-1`).
2. `query_context(world_id, task=..., anchor_name if known, raw=false, token_budget=800)`.
3. `plan_placement(world_id, task=..., anchor_name if known)` → pick top candidate hex(es).
   - Use each candidate's **`center_pos` `[x, z]`** for spawn XZ (world meters).
   - Use **`neighbor_hexes`** when the task spans adjacent cells.
4. **`claim_hexes` is mandatory** before any write:
   - `claim_hexes(world_id, hexes=[...], agent_id=..., ttl_seconds=180)`.
   - On conflict, re-plan with different candidates; do not overwrite foreign leases.
5. **Pre-execute gate**: `critique_region` on the planned anchor / hex set.
   - If critique fails, release claims (or skip claim release if never claimed) and re-plan; **do not spawn**.
6. Sample height then execute via **unity-mcp** `world_bridge`:
   - Prefer `sample_height` at `[x, z]` (or raycast) so Y sits on terrain; fallback sea level / 0 if sampling fails.
   - `spawn` for **individual, indexed landmarks** (torii, lanterns, named rocks) — one call per object, `name` + optional `prefab_path`, `pos: [x, y, z]` from `center_pos` + height (offset slightly so they do not stack).
   - `scatter` for **bulk placement** (groves, undergrowth, rock fields) — one call per patch, not one call per instance:
     - `prefab_paths` (+ optional `prefab_weights`), `pos`/`pos_x`+`pos_z` = patch center, `radius`, `count`, `min_spacing`.
     - `y_mode="analytic"` (default) ground-snaps every instance via the block-world density scan — works in **Edit Mode**, no MeshCollider needed. `avoid_below_y` keeps instances out of ponds/water; `max_slope_delta` rejects cliff faces.
     - `parent_name` (e.g. `"ForestPatch Cherry x110"`) becomes the one indexed GameObject for the whole patch; children are auto-named `__<prefab>_NNN` and are index-ignored — this keeps hex summaries and RAG docs at patch granularity instead of one row per tree.
     - Deterministic: same `seed` + same terrain config reproduces the same layout. Pin a `seed` per patch if the craft may be re-run.
   - `carve_terrain` only in **Play Mode** when a block/voxel bootstrapper is active and Auto-Wire is present; prefer **Edit Mode** for durable spawns and all scatters (analytic Y does not see runtime carves, so do not carve under a scattered patch).
7. Confirm diffs reached the DB:
   - Ensure Live Diff Watcher is on (or flush via World Context window).
   - Re-`query_context` / `list_objects_in_hex` / `get_hex` until new objects appear and dirty hexes clear (summaries refresh on query).
8. `release_hexes` for the claimed set.
9. Final `critique_region` on the worked region; report pass/fail + hex ids + guids.

## Rules

- Bulk index/diff is HTTP-only (Unity World Context window / ChangeWatcher). Never re-index object-by-object through chat.
- **Validate before execute**: claim + pre-critique gate are required; never skip for multi-hex crafts.
- `carve_terrain` **is implemented** (Play Mode + `WorldContextGeneratorBridge` via Auto-Wire). Spawns and scatters should stay in Edit Mode so they persist; re-index (replace) after any Play session if carves or ephemeral objects diverged.
- `scatter`'s `y_mode=analytic` reproduces the exact block-density surface **only** if the terrain config's `seed` is non-zero and unchanged since the last placement — never edit `block_voxel_world` config mid-craft. Runtime SDF carves are invisible to the analytic sampler, so don't carve under a patch you've already scattered.
- Generators stay modular; use `world_bridge` / Auto-Wire rather than calling generator APIs directly unless debugging.
- Never name props with chunk prefixes (`VoxelChunk_`, `BlockChunk_`, `Chunk_`, …) — they are ignored by the indexer.
- Camp / tent naming: avoid substring collisions with separation rules (e.g. name parts `Tent` not `camp` if a camp-separation rule matches "camp").
