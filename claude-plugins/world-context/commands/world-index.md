---
description: Guide a full Unity scene index into Whiskers Agent world_semantic_plugin (bulk HTTP, no LLM dump)
argument-hint: "[world_id]"
---

You are coordinating a **World Semantic Step-1 full index**.

World id (if provided): $ARGUMENTS

## Rules

1. **Never** dump the full scene hierarchy into this conversation or re-index object-by-object via MCP.
2. Bulk path is **Unity → HTTP** `POST /api/world/{world_id}/index`.
3. Generators (`World2D`, `WorldVoxel3D`, `WorldBlockVoxel3D`) stay untouched.

## Steps

1. Confirm Whiskers Agent MCP is reachable. Prefer a lightweight tool (e.g. list tools / describe_region with a test world) only if needed.
2. Instruct the user (or unity-mcp menu if available) to:
   - Open Unity **Window → World Context → Indexer**
   - Ensure `WorldContextConfig.worldId` matches the target world id
   - Ensure `baseUrl` points at Whiskers Agent (e.g. `http://127.0.0.1:8000`)
   - Click **Full Index (replace)**
3. After they confirm success, optionally call:
   - `query_context(world_id=..., anchor_name=..., k=1, raw=true)` on a known object name
   - or report that index completed and they can `/world-query`

If the World Context package is missing, point them at
`Packages/com.cclemon.worldcontext/` in the Unity project.
