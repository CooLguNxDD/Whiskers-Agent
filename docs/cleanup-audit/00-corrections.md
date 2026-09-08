# Audit corrections — read before executing any report in this directory

Reports `01`–`10` were produced by ten parallel Jules audit sessions against
`cleanup/strip-weltel-opencat-tunnel-refs` @ `85e5b926` (PRs #287–#296). They are a
useful inventory, but several load-bearing claims are **wrong**. Each row below was
verified against the tree at that commit before the rebrand work started.

| Report claim | Reality |
|---|---|
| `opencat_mcp.py` entrypoint / compat shim must be renamed (#293, #294) | No such file exists. The entrypoint is already `whiskers_agent_mcp.py`; only stale *comments* name the old file. |
| Docker containers `open-cat-mcp-server` / `open-cat-mcp-frontend` (#291, #293) | Already `whiskers-agent-server` / `whiskers-frontend`. Only the `POSTGRES_USER` / `POSTGRES_DB` defaults were still `open-cat` / `open-cat_mcp`. |
| `opencat_api_` is a production API-token prefix (#294) | Only a literal inside `test/integration/test_plugin_routes.py`. Production prefixes are built as `f"{plugin_id}__"`. |
| The `opencat-goap` MCP server key needs migrating (#294) | Production already emits `whiskers-goap`. `opencat-goap` survived only as a dead fallback branch in two tests. |
| The `opencat` OAuth scope must be migrated (#290, #291, #294) | Already migrated by `core_047_scope_cutover`. It now exists **only** as a frozen historical entry in `core/scope_management/legacy_map.py`, which `core_047` duplicates and `test_scope_cutover_migration.py` cross-checks. Deleting it breaks that test — it must stay. |
| The `tunnels` key in the health payload is branding (#288, #294) | `_count_tunnels()` counts upstream proxies. Legitimate networking term, not the product name. Left as-is. |
| Portfolio/job ids such as `opencat_successor_992` need a data migration (#287, #296) | Fixture-only ids, confined to tests. No migration needed. |

## Scope decisions taken on top of the reports

- **Hard cut.** No dual-accept aliases and no deprecation window: one migration rewrites
  stored scopes, code and tests flip in the same PR, existing API keys/tokens get re-issued.
- **`core:tunnel.*` → `core:whiskers.*`.**
- **Bare "cat" names stay.** `cat_terminal_relay_plugin` (and its `cat_terminal_relay_plugin__*`
  MCP tool names) and `cat-admin-frontend` keep their names — cat theming survives the rebrand,
  and renaming them would break live MCP tool names for pure churn. Only the old *product* name
  (`cat-tunnel-*`, `catTunnel.*`, `"Cat Tunnel"`) counts as legacy branding.
