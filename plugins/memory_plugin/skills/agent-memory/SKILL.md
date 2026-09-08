---
name: agent-memory
description: Persistent semantic memory via memory_plugin MCP tools. USE FOR remembering user facts, preferences, prior workflow notes, and inspecting harness plan recipes.
---

# Agent memory

## Tools (same store as core harness)

| Tool | When |
|------|------|
| `save_memory` | Persist a durable fact/note the user wants remembered |
| `search_memory` | Recall notes before answering ("what did we decide…") |
| `list_memories` | Browse recent notes (not for semantic recall) |
| `delete_memory` | Remove a wrong/stale note by id |
| `search_plan_recipes` | Inspect learned successful op chains |
| `search_anti_patterns` | Inspect failed plan sequences |

## Rules

1. **Never invent memory** — only save content the user provided or explicitly confirmed.
2. **Search before re-asking** — if the user refers to prior context, call `search_memory` first.
3. **Tags** — use short tags (`pref`, `project`, `workflow`) when saving for easier filters.
4. **Collections** — agents write `global_memory` notes; use `collection=core_instructions` to upsert **global harness rules** (DB JSON). Plan recipes / anti-patterns are harness-managed; agents may *search* them. Plugin namespaces look like `{plugin_id}__{name}` when registered.
5. **Tenant** — vector ops are tenant-scoped automatically; harness instructions are instance-global.
6. **Search engine** — free-form document indexing stays on `search_plugin` (`semantic_index` / `semantic_search` → `search_content_vectors`), not memory_plugin.
