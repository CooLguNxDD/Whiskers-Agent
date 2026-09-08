---
name: context-discovery
description: Discover and index GitHub/Notion portfolio context into portfolio_plugin__context.
goal_classes: [discover]
triggers: [discover, reindex, index github, refresh inventory]
---

# Context discovery — portfolio indexing

## When to use

- Operator wants to **discover** GitHub/Notion project context into the semantic index.
- Content inventory is thin / hardcoded and should be refreshed from live proxies.
- Never for anonymous visitor chat turns — discovery tools are **GOAP-denylisted**.
- Prefer the **specialist discovery agent** (multi-stack graph) over one-off scripts.

## Tools / agents

| Surface | Role |
|---------|------|
| `discover_portfolio_context(scope, dry_run, force)` | MCP tool; routes through `run_discovery_agent` (agentic-first, see below); default `dry_run=True` |
| `rebuild_portfolio_index(force, scope)` | MCP tool; index only into `portfolio_plugin__context` |
| `run_discovery_agent` (`plugins.portfolio_plugin.agents`) | Portfolio phase; **agentic-first** (LLM tool-calling loop via `core_graph.agent_loop`, see below) with deterministic-pipeline fallback; honest proxy-missing errors + draft fold |
| `run_index_agent` | Specialist phase; index write without project promote |

**`dry_run` gates project writes, not which planner runs.** The agentic loop is bound to
read-only tool globs only (`_discovery_tool_refs()`), so it's safe to run under
`dry_run=True` too. Normal runs index when `do_index=True`; a dry run indexes only with the
explicit `index_on_dry_run=True` opt-in (ask mode maps this from `ask.discovery_index`).
`write_back` still controls project reconciliation and is always forced off for public asks.
Pass `use_agentic=False` to force the deterministic sweep regardless of LLM availability.
Two entrypoints stay deterministic-only on purpose — the scheduled worker
(`discovery/worker.py::_run_once`) and the `on_ready` bootstrap sweep
(`plugin_config.py`) — since both fire without a request principal or user
intent, and spending LLM budget on every unattended tick isn't wanted.

**Do not use deleted CLI scripts** (`scripts/discover_portfolio.py`, `scripts/mcp_driven_portfolio_index_bake.py`). Drive discovery via MCP / specialist stack inside the live server so OperationCatalog proxies resolve.

Example (MCP / nested run_graph / PortfolioAgent):

```
discover_portfolio_context(scope="all", dry_run=true)
# promote when ready:
discover_portfolio_context(scope="all", dry_run=false)  # write_back still gated by settings
rebuild_portfolio_index(scope="all")
```

## Pipeline (agentic-first, deterministic fallback)

0. **Agent loop** (`core_graph/agent_loop/`, generic — not portfolio-specific) — when an
   LLM is configured (`llm_available()`), `run_discovery_agent` runs an `AgentSpec`
   (prompt: `specialist/prompts/discovery_prompt.py`) that calls `list_owned_repos` /
   `proxy_github-*` search itself and *judges* which repos are portfolio-worthy
   (recent activity, real content, non-fork/non-archived) instead of only matching a
   static manifest allowlist. Findings feed the same normalize/index/reconcile below.
   Falls back to step 1 when no LLM is available or the agent found nothing.
1. **Capability resolver** — manifest `settings.discovery.sources` binds *intents* (`github.repos.list`, `notion.pages.search`) to live `OperationCatalog` proxy ops (`prefer` globs + `operation_contains`). Fallback: local `list_owned_repos`. Proxy-detection helpers (`is_proxy_op`/`prefer_match`) now canonically live in `core_graph.agent_loop.toolset`; `resolver.py` imports them.
2. **Normalize / guard / index** — GitHub allowlist (owner-wide + token-login by default — `strict_allowlist_repos: false`; manifest `repos[]` is a pin list, not a gate), Notion `notion_allowlist` (databases / page_prefixes), strip directive-shaped text, quality bar, sha256-deduped embed into `portfolio_plugin__context`. `index_docs` deletes prior rows for a `ref` before inserting (`replace_stale`, default on) so a changed README actually refreshes `meta.indexed_at` instead of freezing at first-index time under content-hash dedupe.
3. **Retrieve** — compose/bake/intent rank projects via the index; `_live_enrich` is index-first and only hits the network past `freshness_s`. Slug matching (`discovery/slug.py::keys_match`) also checks a project's `context_sources[].ref` / `links[].href`, since no slug-generation rule can bridge a hand-authored slug (`oct`) to a discovered one (`opencat-mcp-full`). When no `PortfolioProject` row matches, `block_builder._resolve_projects` synthesizes virtual project dicts straight from indexed hits — content isn't gated on write-back having run first.

## Write-back

- Gated by `settings.discovery.write_back` (default **true**) and explicit non-dry MCP calls.
- Provenance in `context_sources[].id` prefixes: `disc:github:owner/repo`.
- Never overwrite hand-authored `summary` / `metrics`.
- No seed script / hardcoded inventory. Projects come from discovery write-back or manual MCP `upsert_project`.
- `portfolio_star` / `add_star_story` are retired. Bake treats `portfolio_projects` + `portfolio_plugin__context` as **planner context only** (not the tool result).
- `scheduled_jobs.portfolio_discovery` is enabled by default; `tenant_ids` (list, default `[1]`) drives which tenants the worker sweeps — no hardcoded tenant.

## Rules

- Proxy callables never raise — check `status`. Never prune kwargs for proxies. Pass `_response_shape={"response_format":"json"}`.
- Refs are **never** derived from visitor chat.
- Indexed docs may **never** invent `context_sources` on the visitor path.
- `GET /api/portfolio/public/layout` must make **zero** outbound third-party calls.
- Explicit `current_tenant_id` on worker / boot / agent paths.
- Missing Notion proxy when scope includes notion → **error/partial** (never silent empty success).

## Phase 7 (not built)

Approval queue + CatAdmin UI + LLM STAR extraction — design reconcile plan so it can divert into a queue later.
