---
description: Answer a spatial question about the indexed Unity world via Whiskers Agent MCP
argument-hint: "<question, e.g. what's north of the player camp?> [world_id=demo]"
---

Answer this spatial question using **Whiskers Agent `world_semantic_plugin` MCP tools only** (not a full scene dump).

Question / args: $ARGUMENTS

## Procedure

1. Parse `world_id` if present (default `default` or last known). Parse anchor names and directions (north/south/east/west).
2. Call `describe_region` and/or `query_context`:
   - `anchor_name` = best matching object name substring
   - `direction` = north|south|east|west when asked
   - `k` = 2–4 depending on “nearby” vs “region”
   - `raw=true` (step 1 — no encoder yet)
3. Summarize the tool result for the user: counts, object names, hex ids, positions.
4. If `world_not_found` or `anchor_not_found`, tell the user to run `/world-index` and ensure the named object exists in the scene.

Do **not** call unity-mcp for bulk hierarchy walks unless the user explicitly needs live editor inspection beyond the DB.
